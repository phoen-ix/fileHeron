"""Streaming ZIP builder for bulk share downloads.

On-the-fly, **ZIP_STORED** (no compression), O(1) memory - we never build or
cache a zip on disk. A cached archive would double bytes on the bind mount and
create a second copy that GDPR-erasure / expiry would have to find and reap,
which is exactly the single-server-delete-simplicity property we want to keep.

`zip_writer.SizedZipStream` (STORED-only) lets us compute the exact archive
length before streaming, so the response can carry a real `Content-Length`
(browser progress bar + Range-resume) while still yielding the bytes lazily -
each member is read from disk in chunks as the archive streams, never buffered
whole. Shared files are almost always already-compressed (media, PDFs, office,
archives), so STORED costs ~nothing vs. DEFLATE and avoids burning CPU on 30 GB.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import StreamingResponse

from ..models.file import File
from .zip_writer import SizedZipStream


def safe_arcname(name: str, taken: set[str]) -> str:
    """Reduce an arbitrary stored filename to a safe, unique archive entry name.
    The ZIP writer does NOT sanitize arcnames, so a stored name like
    `../../etc/passwd` would otherwise land verbatim. Strip directory components
    + null bytes, then de-duplicate collisions with a ` (n)` suffix."""
    base = Path(name).name.replace("\x00", "").strip() or "file"
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


def build_zip_stream(files: list[File]) -> SizedZipStream:
    """Build a sized `SizedZipStream` over `files` (each must have bytes in
    storage). `len(zs)` gives the exact Content-Length; iterating it streams the
    archive. The caller filters to downloadable (`clean`, bytes-present) files
    first."""
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    zs = SizedZipStream()
    taken: set[str] = set()
    for f in files:
        arcname = safe_arcname(f.original_filename or "file", taken)
        lp = backend.local_path(f.storage_path)
        if lp is not None:
            zs.add_path(lp, arcname)  # local disk → add by path (today's path)
        else:
            # Object store → open lazily at streaming time. The previous
            # implementation called backend.open() here, during construction,
            # so a share with N members held N open object-store readers before
            # a single byte was sent.
            path = f.storage_path
            zs.add_stream(
                (lambda p=path: backend.open(p)), arcname, size=f.size_bytes
            )
    return zs


def zip_streaming_response(
    files: list[File], archive_basename: str, *, count: bool = False
) -> StreamingResponse:
    """A `StreamingResponse` that streams a ZIP_STORED archive of `files` with an
    exact `Content-Length` (sized mode) so the browser shows real progress and
    can Range-resume. `archive_basename` becomes `<basename>.zip` in the
    attachment filename.

    `count=True` registers the stream as an in-flight download
    (services/transfer_activity) so the maintenance-mode drain knows when it
    finishes - decremented in the generator's `finally`, which fires even when the
    client disconnects mid-stream."""
    zs = build_zip_stream(files)
    length = len(zs)

    body = iter(zs)
    if count:
        from . import transfer_activity

        def _counted():
            dl_id = transfer_activity.download_started()
            try:
                yield from zs
            finally:
                transfer_activity.download_finished(dl_id)

        body = _counted()

    return StreamingResponse(
        body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_basename}.zip"',
            "Content-Length": str(length),
        },
    )
