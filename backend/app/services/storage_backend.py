"""Pluggable storage backend (v1.21.0).

All file-byte I/O - finalize, download, read (zip / AV), delete, quarantine -
routes through a `StorageBackend` so the bytes can live on the local bind mount
(default) or, opt-in, an object store, without the upload / download / AV /
quarantine / delete code knowing which.

`File.storage_path` holds a backend-interpreted **locator**: the local backend
uses the absolute on-disk path (identical to before this abstraction, so existing
rows keep working with no migration); an object backend uses its object key.

PR-A (this) ships only `LocalFilesystemBackend` - a byte-for-byte wrapper of the
prior behaviour. PR-B adds an opt-in `S3Backend` and `STORAGE_BACKEND` selection.
"""
from __future__ import annotations

import logging
import re
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, cast

from fastapi.responses import FileResponse

from ..config import settings
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.storage_backend")


class StorageBackend(ABC):
    name: str = "base"
    # True when bytes live on a local filesystem - enables kernel-sendfile
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
        self,
        *,
        locator: str,
        filename: str,
        mime_type: str,
        ttl_sec: int,
        disposition: str = "attachment",
    ) -> str | None:
        """A presigned URL to redirect the browser to (object stores); None when
        the file is served directly via FileResponse(local_path). `disposition`
        is "attachment" (download) or "inline" (preview)."""

    @abstractmethod
    def delete(self, locator: str) -> None:
        """Idempotent - a missing object is not an error."""

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
    """The bind-mount backend - byte-for-byte the behaviour before the
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
        # cross-device inside the container - Errno 18 EXDEV).
        shutil.move(str(src_temp_path), str(dest))

    def open(self, locator: str) -> BinaryIO:
        return open(locator, "rb")

    def local_path(self, locator: str) -> str | None:
        return locator

    def download_url(
        self, *, locator, filename, mime_type, ttl_sec, disposition="attachment"
    ) -> str | None:
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


class S3Backend(StorageBackend):
    """Opt-in S3-compatible object store (AWS S3, MinIO, etc.). `File.storage_path`
    holds the object key. Downloads 307-redirect to a presigned GET; AV scans via
    clamd INSTREAM (no shared mount); quarantine is a server-side copy between
    key prefixes. `STORAGE_BACKEND=s3` selects it; local stays the default."""

    name = "s3"
    supports_disk_stats = False

    def __init__(self) -> None:
        import boto3

        self._bucket = settings.S3_BUCKET
        self._prefix = settings.S3_KEY_PREFIX
        kwargs: dict = {"region_name": settings.S3_REGION}
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        if settings.S3_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        self._s3 = boto3.client("s3", **kwargs)

    def generate_locator(self, file_id: str, when: datetime | None = None) -> str:
        when = when or utc_now()
        return f"{self._prefix}{when.year:04d}/{when.month:02d}/{file_id}.bin"

    def finalize(self, src_temp_path: str, locator: str) -> None:
        import os

        # boto3 upload_file does multipart automatically for large files.
        self._s3.upload_file(src_temp_path, self._bucket, locator)
        try:
            os.unlink(src_temp_path)
        except OSError:
            pass

    def open(self, locator: str) -> BinaryIO:
        # botocore returns a StreamingBody: not nominally a BinaryIO, but it
        # implements the read/close surface every consumer here uses (zip
        # building and clamd INSTREAM both only read).
        return cast("BinaryIO", self._s3.get_object(Bucket=self._bucket, Key=locator)["Body"])

    def local_path(self, locator: str) -> str | None:
        return None

    def download_url(
        self, *, locator, filename, mime_type, ttl_sec, disposition="attachment"
    ) -> str | None:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": locator,
                "ResponseContentDisposition": _content_disposition(disposition, filename),
                "ResponseContentType": mime_type,
            },
            ExpiresIn=ttl_sec,
        )

    def delete(self, locator: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._s3.delete_object(Bucket=self._bucket, Key=locator)  # idempotent on s3
        except (ClientError, BotoCoreError) as e:
            # Raise OSError so callers' `except OSError` (erasure, file.hard_delete)
            # engage backend-neutrally instead of a botocore error escaping.
            raise OSError(f"s3 delete failed for {locator}: {e}") from e

    def exists(self, locator: str) -> bool:
        from botocore.exceptions import ClientError

        if not locator:
            return False
        try:
            self._s3.head_object(Bucket=self._bucket, Key=locator)
            return True
        except ClientError:
            return False

    def size(self, locator: str) -> int:
        return int(self._s3.head_object(Bucket=self._bucket, Key=locator)["ContentLength"])

    def move(self, src_locator: str, dst_locator: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._s3.copy_object(
                Bucket=self._bucket,
                Key=dst_locator,
                CopySource={"Bucket": self._bucket, "Key": src_locator},
            )
            self._s3.delete_object(Bucket=self._bucket, Key=src_locator)
        except (ClientError, BotoCoreError) as e:
            # OSError so quarantine's `except OSError` catches a transient S3
            # error instead of aborting the whole quarantine uncaught.
            raise OSError(f"s3 move failed {src_locator}->{dst_locator}: {e}") from e

    def quarantine_locator(self, share_id: str, filename: str) -> str:
        return f"{self._prefix}quarantine/{share_id}/{filename}"


def _content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266 / RFC 5987 Content-Disposition. Emits an ASCII-safe filename=""
    fallback plus a percent-encoded filename*=UTF-8'' for the real (possibly
    non-ASCII, quote-bearing) name - the previous naive f-string produced broken
    or garbled download filenames (and could inject header tokens) on S3."""
    from urllib.parse import quote

    # Filter to printable ASCII rather than blacklisting two characters. This
    # builder is reached with names nobody in this codebase sanitised: inbound
    # mail stores the attachment filename straight off an attacker-authored
    # MIME header, and a CR/LF that survives into the filename="" parameter
    # gets the whole presigned download rejected by the object store.
    ascii_name = (
        "".join(
            c
            for c in filename.encode("ascii", "ignore").decode()
            if " " <= c < "\x7f" and c not in '"\\'
        )
        or "download"
    )
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"



# RFC 9110 token characters. A media type outside this shape has no business
# reaching a response header.
_MEDIA_TYPE_RE = re.compile(r"^[!#$%&\'*+\-.^_`|~0-9A-Za-z]+/[!#$%&\'*+\-.^_`|~0-9A-Za-z]+$")
_FALLBACK_MEDIA_TYPE = "application/octet-stream"


def safe_media_type(mime_type: str | None) -> str:
    """Clamp a stored media type to something safe to put in a header.

    `files.mime_type` is whatever the client announced at upload. The preview
    routes already pinned it through preview.safe_content_type, but the two
    DOWNLOAD routes passed it through verbatim - so a stored type containing a
    control character (CR/LF in particular) reached the Content-Type header. In
    practice the ASGI server rejects it and the file becomes permanently
    undownloadable rather than the header being split, but neither outcome is
    acceptable and the public-link route makes it anonymously reachable
    (audit 2026-07-30).

    Parameters are dropped deliberately: nothing here needs them, and they are
    the part that carries quoted strings.
    """
    if not mime_type:
        return _FALLBACK_MEDIA_TYPE
    base = mime_type.split(";", 1)[0].strip()
    if not _MEDIA_TYPE_RE.match(base):
        return _FALLBACK_MEDIA_TYPE
    return base


class _CountedFileResponse(FileResponse):
    """FileResponse that releases its drain-counter entry no matter how the
    response ends.

    The release used to ride on `FileResponse(background=...)`, and Starlette
    only runs a BackgroundTask after a response has been sent. An unsatisfiable
    or malformed `Range` header raises inside `FileResponse.__call__` BEFORE
    anything is sent, so the entry registered a moment earlier was never
    released - it sat in the ZSET until the 6-hour age prune, holding the
    drain-before-update open against a transfer that never happened. One
    `curl -H 'Range: bytes=99999999-'` per phantom (audit 2026-07-30).

    `finally` covers the send path, the raise path and client disconnect
    alike, which is the same shape zip_stream.py already uses for its own
    counter."""

    def __init__(self, *args, dl_id: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._dl_id = dl_id

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._dl_id is not None:
                from . import transfer_activity

                transfer_activity.download_finished(self._dl_id)


def serve_response(
    backend: StorageBackend,
    *,
    locator: str,
    filename: str,
    mime_type: str,
    ttl_sec: int,
    disposition: str = "attachment",
    extra_headers: dict[str, str] | None = None,
    count: bool = False,
    file_id: str | None = None,
):
    """The HTTP response for a file. Local backend → FileResponse (kernel
    sendfile, Range-capable); object backend → 307 redirect to a presigned URL
    so the browser fetches/resumes bytes from the store directly.

    `disposition` is "attachment" (download, default - preserves every existing
    caller) or "inline" (preview). `extra_headers` (preview hardening:
    nosniff/CSP) ride on the local FileResponse; on the S3 redirect the bytes
    come from the store and can't carry them, so the previewable-type allowlist
    is the defense there (documented caveat).

    `count=True` registers this as an in-flight download (services/transfer_activity)
    so the maintenance-mode drain knows when transfers finish. Only the local
    FileResponse can be tracked - an S3 redirect streams bytes the backend never
    sees, so it is not counted."""
    from fastapi.responses import RedirectResponse

    mime_type = safe_media_type(mime_type)

    url = backend.download_url(
        locator=locator,
        filename=filename,
        mime_type=mime_type,
        ttl_sec=ttl_sec,
        disposition=disposition,
    )
    if url is not None:
        # Object store: the client fetches the bytes straight from the bucket, so
        # this process never sees them and cannot count the stream for the
        # maintenance drain - that limitation is inherent and documented.
        #
        # The recency MARK is a different thing and was lost with it, purely
        # because the redirect returned before the line that writes it. On S3
        # `was_download_recent` was therefore always False, so the maintenance
        # gate refused every genuine resumed download during a drain: strictly
        # more restrictive than intended, not a bypass, and invisible on a local
        # deployment (audit 2026-07-30 residual sweep, res-03).
        #
        # Marked here, without a drain registration: there is no stream to
        # finish, so there is nothing to decrement and no leaked ZSET entry.
        if count and file_id:
            from . import transfer_activity

            transfer_activity.mark_download_recent(file_id)
        return RedirectResponse(url, status_code=307)

    dl_id = None
    if count:
        from . import transfer_activity

        dl_id = transfer_activity.download_started(file_id)
    return _CountedFileResponse(
        path=backend.local_path(locator),
        media_type=mime_type,
        filename=filename,
        content_disposition_type=disposition,
        headers=extra_headers or None,
        dl_id=dl_id,
    )


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """Cached singleton chosen by the STORAGE_BACKEND config (local | s3)."""
    global _backend
    if _backend is None:
        if settings.STORAGE_BACKEND.strip().lower() == "s3":
            _backend = S3Backend()
            logger.info("storage backend: s3 (bucket=%s)", settings.S3_BUCKET)
        else:
            _backend = LocalFilesystemBackend()
    return _backend


def reset_storage_backend_cache() -> None:
    """Test hook - drop the cached backend so a test can swap config/backend."""
    global _backend
    _backend = None
