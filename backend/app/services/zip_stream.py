"""Streaming ZIP builder for bulk share downloads.

On-the-fly, **ZIP_STORED** (no compression), O(1) memory - we never build or
cache a zip on disk. A cached archive would double bytes on the bind mount and
create a second copy that GDPR-erasure / expiry would have to find and reap,
which is exactly the single-server-delete-simplicity property we want to keep.

`zipstream-ng`'s *sized* mode (STORED-only) lets us compute the exact archive
length before streaming, so the response can carry a real `Content-Length`
(browser progress bar + Range-resume) while still yielding the bytes lazily -
each member is read from disk in chunks as the archive streams, never buffered
whole. Shared files are almost always already-compressed (media, PDFs, office,
archives), so STORED costs ~nothing vs. DEFLATE and avoids burning CPU on 30 GB.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import StreamingResponse
from zipstream import ZIP_STORED, ZipStream

from ..models.file import File


def safe_arcname(name: str, taken: set[str]) -> str:
    """Reduce an arbitrary stored filename to a safe, unique archive entry name.
    `zipstream-ng.add_path` does NOT sanitize arcnames, so a stored name like
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


def build_zip_stream(files: list[File]) -> ZipStream:
    """Build a sized `ZipStream` over `files` (each must have bytes in storage).
    `len(zs)` gives the exact Content-Length; iterating it streams the archive.
    The caller filters to downloadable (`clean`, bytes-present) files first."""
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    zs = ZipStream(compress_type=ZIP_STORED, sized=True)
    taken: set[str] = set()
    for f in files:
        arcname = safe_arcname(f.original_filename or "file", taken)
        lp = backend.local_path(f.storage_path)
        if lp is not None:
            zs.add_path(lp, arcname)  # local disk → add by path (today's path)
        else:
            # Object store → stream the object; sized mode needs an explicit size.
            zs.add(backend.open(f.storage_path), arcname, size=f.size_bytes)
    return zs


def zip_streaming_response(files: list[File], archive_basename: str) -> StreamingResponse:
    """A `StreamingResponse` that streams a ZIP_STORED archive of `files` with an
    exact `Content-Length` (sized mode) so the browser shows real progress and
    can Range-resume. `archive_basename` becomes `<basename>.zip` in the
    attachment filename."""
    zs = build_zip_stream(files)
    length = len(zs)
    return StreamingResponse(
        iter(zs),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_basename}.zip"',
            "Content-Length": str(length),
        },
    )
