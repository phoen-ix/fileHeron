"""Pluggable storage backend (v1.21.0).

All file-byte I/O — finalize, download, read (zip / AV), delete, quarantine —
routes through a `StorageBackend` so the bytes can live on the local bind mount
(default) or, opt-in, an object store, without the upload / download / AV /
quarantine / delete code knowing which.

`File.storage_path` holds a backend-interpreted **locator**: the local backend
uses the absolute on-disk path (identical to before this abstraction, so existing
rows keep working with no migration); an object backend uses its object key.

PR-A (this) ships only `LocalFilesystemBackend` — a byte-for-byte wrapper of the
prior behaviour. PR-B adds an opt-in `S3Backend` and `STORAGE_BACKEND` selection.
"""
from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from ..config import settings
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.storage_backend")


class StorageBackend(ABC):
    name: str = "base"
    # True when bytes live on a local filesystem — enables kernel-sendfile
    # downloads, clamd path-scan, and the disk-space guard. False for object stores.
    supports_disk_stats: bool = False

    @abstractmethod
    def generate_locator(self, file_id: str, when: datetime | None = None) -> str:
        """The locator to store in File.storage_path for a new finalized file."""

    @abstractmethod
    def finalize(self, src_temp_path: str, locator: str) -> None:
        """Move bytes from a local temp file (TUS working dir / direct-upload
        temp) into the backend at `locator`. Consumes the temp file."""

    @abstractmethod
    def open(self, locator: str) -> BinaryIO:
        """A readable binary stream (zip building, INSTREAM AV). Caller closes."""

    @abstractmethod
    def local_path(self, locator: str) -> str | None:
        """Absolute on-disk path if the backend is local-disk (enables
        FileResponse sendfile + clamd path-scan); None for object stores."""

    @abstractmethod
    def download_url(
        self, *, locator: str, filename: str, mime_type: str, ttl_sec: int
    ) -> str | None:
        """A presigned URL to redirect the browser to (object stores); None when
        the file is served directly via FileResponse(local_path)."""

    @abstractmethod
    def delete(self, locator: str) -> None:
        """Idempotent — a missing object is not an error."""

    @abstractmethod
    def exists(self, locator: str) -> bool: ...

    @abstractmethod
    def size(self, locator: str) -> int: ...

    @abstractmethod
    def move(self, src_locator: str, dst_locator: str) -> None:
        """Relocate bytes (quarantine in / release out)."""

    @abstractmethod
    def quarantine_locator(self, share_id: str, filename: str) -> str:
        """Locator for a quarantined copy of `filename` under `share_id`."""


class LocalFilesystemBackend(StorageBackend):
    """The bind-mount backend — byte-for-byte the behaviour before the
    abstraction existed (so the full test suite must stay green)."""

    name = "local"
    supports_disk_stats = True

    def generate_locator(self, file_id: str, when: datetime | None = None) -> str:
        when = when or utc_now()
        return str(
            Path(settings.STORAGE_ROOT)
            / f"{when.year:04d}"
            / f"{when.month:02d}"
            / f"{file_id}.bin"
        )

    def finalize(self, src_temp_path: str, locator: str) -> None:
        dest = Path(locator)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # shutil.move == os.rename when same-fs (the documented STORAGE_ROOT /
        # TUS_UPLOAD_DIR requirement), else copy2 + unlink (bind mounts look
        # cross-device inside the container — Errno 18 EXDEV).
        shutil.move(str(src_temp_path), str(dest))

    def open(self, locator: str) -> BinaryIO:
        return open(locator, "rb")

    def local_path(self, locator: str) -> str | None:
        return locator

    def download_url(self, *, locator, filename, mime_type, ttl_sec) -> str | None:
        return None  # served via FileResponse(local_path)

    def delete(self, locator: str) -> None:
        p = Path(locator)
        if p.is_file():
            p.unlink()

    def exists(self, locator: str) -> bool:
        return bool(locator) and Path(locator).is_file()

    def size(self, locator: str) -> int:
        return Path(locator).stat().st_size

    def move(self, src_locator: str, dst_locator: str) -> None:
        dest = Path(dst_locator)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # STORAGE_ROOT and QUARANTINE_DIR are same-fs by config requirement, so
        # this is an atomic rename; shutil.move keeps a copy fallback regardless.
        shutil.move(str(src_locator), str(dest))

    def quarantine_locator(self, share_id: str, filename: str) -> str:
        return str(Path(settings.QUARANTINE_DIR) / share_id / filename)


def serve_response(
    backend: StorageBackend, *, locator: str, filename: str, mime_type: str, ttl_sec: int
):
    """The HTTP download response for a file. Local backend → FileResponse
    (kernel sendfile, Range-capable); object backend → 307 redirect to a
    presigned URL so the browser fetches/resumes bytes from the store directly."""
    from fastapi.responses import FileResponse, RedirectResponse

    url = backend.download_url(
        locator=locator, filename=filename, mime_type=mime_type, ttl_sec=ttl_sec
    )
    if url is not None:
        return RedirectResponse(url, status_code=307)
    return FileResponse(
        path=backend.local_path(locator),
        media_type=mime_type,
        filename=filename,
        content_disposition_type="attachment",
    )


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """Cached singleton chosen by config (PR-B adds the s3 branch)."""
    global _backend
    if _backend is None:
        _backend = LocalFilesystemBackend()
    return _backend


def reset_storage_backend_cache() -> None:
    """Test hook — drop the cached backend so a test can swap config/backend."""
    global _backend
    _backend = None
