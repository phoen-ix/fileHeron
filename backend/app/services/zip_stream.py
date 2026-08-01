"""Streaming ZIP builder for bulk share downloads.

On-the-fly, **ZIP_STORED** (no compression), O(1) memory - we never build or
cache a zip on disk. A cached archive would double bytes on the bind mount and
create a second copy that GDPR-erasure / expiry would have to find and reap,
which is exactly the single-server-delete-simplicity property we want to keep.

`zip_writer.SizedZipStream` (STORED-only) lets us compute the exact archive
length before streaming, so the response carries a real `Content-Length` (a true
browser progress bar) while still yielding the bytes lazily - each member is read
from storage in chunks as the archive streams, never buffered whole. Shared files
are almost always already-compressed (media, PDFs, office, archives), so STORED
costs ~nothing vs. DEFLATE and avoids burning CPU on 30 GB.

RESUME
------
Because the layout is arithmetic, the archive is also *seekable*, and these
responses honour `Range` for real (audit 2026-07-30, flow-publiclink-5: a 9 GB
archive that died at 90% used to be unrecoverable - the link was already charged
and every retry started again from byte 0, or got a 410 once the budget ran out).

Three things make a resume safe rather than merely possible:

- the archive is **reproducible** - the DOS timestamp comes from the share's
  creation time, not the clock, and `file.downloadable_files` orders members
  totally, so generating it twice gives identical bytes;
- the **ETag** covers the member list, their sizes and the layout version, so a
  member quarantined or added between attempts makes `If-Range` miss and the
  client restarts cleanly instead of splicing two different archives;
- a member's **CRC** is cached in Redis as the full stream computes it, so
  resuming past it does not mean re-reading it. On a miss the writer re-reads
  that member; when that would cost more than `MAX_RESUME_REREAD_BYTES` we serve
  the whole archive with a 200 instead. Never a guessed CRC.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi.responses import Response, StreamingResponse

from ..models.file import File
from .zip_writer import SizedZipStream

logger = logging.getLogger("fileheron.zip_stream")

# A member's bytes never change once it is `clean`, so its CRC is cacheable
# forever; the TTL only bounds junk from files that are later deleted.
_CRC_KEY_PREFIX = "fh:zip:crc:"
CRC_TTL_SEC = 7 * 24 * 3600

# How much wasted read a resume may cost before it stops being worth it. Two
# gigabytes is roughly a second or two off local disk, and a client that would
# have to wait longer than that for its first resumed byte is better served by
# a clean restart it can see progressing.
MAX_RESUME_REREAD_BYTES = 2 * 1024 * 1024 * 1024


class RedisCrcCache:
    """`zip_writer.CrcCache` over Redis. Every method fails soft: a cache miss
    costs a re-read, a cache outage costs a full-archive 200, and neither is
    allowed to fail the download."""

    def get(self, key: str) -> int | None:
        from ..redis_client import get_redis

        try:
            raw = get_redis().get(_CRC_KEY_PREFIX + key)
        except Exception:
            logger.warning("zip crc cache unavailable", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):  # pragma: no cover - corrupt value
            return None

    def put(self, key: str, crc: int) -> None:
        from ..redis_client import get_redis

        try:
            get_redis().set(_CRC_KEY_PREFIX + key, str(crc), ex=CRC_TTL_SEC)
        except Exception:
            logger.warning("zip crc cache write failed", exc_info=True)


def safe_arcname(name: str, taken: set[str]) -> str:
    """Reduce an arbitrary stored filename to a safe, unique archive entry name.
    The ZIP writer does NOT sanitize arcnames, so a stored name like
    `../../etc/passwd` would otherwise land verbatim. Strip directory components
    + null bytes, then de-duplicate collisions with a ` (n)` suffix.

    Backslashes are separators here too. `Path(...).name` treats them as
    ordinary characters on Linux, so `..\\..\\Users\\Public\\Startup\\x.exe`
    came through whole and 7-Zip on Windows would write it outside the target
    directory. A leading-dots-only name (`..`) is not a filename either
    (audit #2). No route writes such a row today - this is defence in depth
    that was not holding."""
    base = Path(name.replace("\\", "/")).name.replace("\x00", "").strip() or "file"
    if set(base) <= {"."}:
        base = "file"
    if base not in taken:
        taken.add(base)
        return base
    stem, suffix = Path(base).stem, Path(base).suffix
    i = 1
    while True:
        candidate = f"{stem} ({i}){suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        i += 1


def build_zip_stream(
    files: list[File], *, mtime: float | None = None, crc_cache=None
) -> SizedZipStream:
    """Build a sized `SizedZipStream` over `files` (each must have bytes in
    storage). `len(zs)` gives the exact Content-Length; iterating it streams the
    archive. The caller filters to downloadable (`clean`, bytes-present) files
    first.

    `mtime` is the DOS timestamp stamped on every member - pass the share's
    creation time so the same share always produces the same bytes; the default
    is a fixed epoch, never the clock."""
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    zs = SizedZipStream(mtime=mtime, crc_cache=crc_cache)
    taken: set[str] = set()
    for f in files:
        arcname = safe_arcname(f.original_filename or "file", taken)
        lp = backend.local_path(f.storage_path)
        if lp is not None:
            zs.add_path(lp, arcname, cache_key=f.id)  # local disk → add by path
        else:
            # Object store → open lazily at streaming time. The previous
            # implementation called backend.open() here, during construction,
            # so a share with N members held N open object-store readers before
            # a single byte was sent.
            path = f.storage_path
            zs.add_stream(
                (lambda p=path: backend.open(p)),
                arcname,
                size=f.size_bytes,
                cache_key=f.id,
            )
    return zs


def zip_identity(files: list[File], *, mtime: float | None = None) -> tuple[str, int]:
    """(ETag, exact byte length) of the archive `files` would produce.

    The route needs both before it can answer a `Range` or an `If-Range`, and
    both are pure arithmetic over names and sizes - no member is opened."""
    zs = build_zip_stream(files, mtime=mtime)
    return zs.signature(), len(zs)


def zip_streaming_response(
    files: list[File],
    archive_basename: str,
    *,
    count: bool = False,
    mtime: float | None = None,
    byte_range: tuple[int, int] | None = None,
    etag: str | None = None,
) -> Response:
    """Stream a ZIP_STORED archive of `files` with an exact `Content-Length`, so
    the browser shows real progress. `archive_basename` becomes `<basename>.zip`
    in the attachment filename.

    `byte_range` (inclusive start/end, already validated against `zip_length`)
    turns this into a 206 with `Content-Range`. It is honoured only when the
    writer can reach that offset without re-reading more than
    `MAX_RESUME_REREAD_BYTES`; past that the full archive is returned with a
    200, which is always a correct answer to a Range request and beats making
    the client wait minutes for its first byte.

    `count=True` registers the stream as an in-flight download
    (services/transfer_activity) so the maintenance-mode drain knows when it
    finishes - decremented in the generator's `finally`, which fires even when
    the client disconnects mid-stream.

    There used to be a `recent_key` here, documented as "the evidence a later
    resume needs". Nothing read it: both ZIP routes corroborate a resume with
    the principal-keyed PAYMENT mark, and removing the write left every resume
    behaviour unchanged (measured). It was a Redis write with a 30-minute TTL
    against a `noeviction` instance, and a docstring telling a maintainer the
    archive resume had a serving-side corroboration it does not have
    (audit #2)."""
    zs = build_zip_stream(files, mtime=mtime, crc_cache=RedisCrcCache())
    total = len(zs)

    status = 200
    start, length = 0, total
    headers = {
        "Content-Disposition": f'attachment; filename="{archive_basename}.zip"',
        "Accept-Ranges": "bytes",
    }
    if etag is not None:
        headers["ETag"] = f'"{etag}"'

    if byte_range is not None:
        req_start, req_end = byte_range
        cost = zs.resume_cost(req_start)
        if cost > MAX_RESUME_REREAD_BYTES:
            logger.info(
                "zip resume at %d declined: would re-read %d bytes", req_start, cost
            )
        else:
            status = 206
            start = req_start
            length = req_end - req_start + 1
            headers["Content-Range"] = f"bytes {req_start}-{req_end}/{total}"

    headers["Content-Length"] = str(length)

    body = zs.iter_from(start, length)
    if count:
        from . import transfer_activity

        def _counted():
            dl_id = transfer_activity.download_started(None)
            try:
                yield from zs.iter_from(start, length)
            finally:
                transfer_activity.download_finished(dl_id)

        body = _counted()

    return StreamingResponse(
        body, status_code=status, media_type="application/zip", headers=headers
    )
