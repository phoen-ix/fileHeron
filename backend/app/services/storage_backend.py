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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

from fastapi.responses import FileResponse

from ..config import settings
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .file_encryption import StoredCipher

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

    # --- encryption at rest (services/file_encryption.py) -------------------

    @abstractmethod
    def open_range(self, locator: str, start: int, end_incl: int) -> BinaryIO:
        """A stream of bytes `[start, end_incl]` only. Encrypted files are read
        a chunk window at a time (Range, ZIP resume, the seekable reader)."""

    @abstractmethod
    def write_stream(self, locator: str, reader) -> None:
        """Create `locator` from a readable stream - refusing to overwrite one
        that exists - and make it durable before returning."""

    @abstractmethod
    def sibling_locator(self, locator: str, name: str) -> str:
        """A locator named `name` beside `locator` (same directory / prefix)."""


class _BoundedReader:
    """At most `remaining` bytes of an already-positioned file."""

    def __init__(self, raw: BinaryIO, remaining: int) -> None:
        self._raw = raw
        self._remaining = remaining

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        n = self._remaining if size is None or size < 0 else min(size, self._remaining)
        data = self._raw.read(n)
        self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> _BoundedReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


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

    def open_range(self, locator: str, start: int, end_incl: int) -> BinaryIO:
        # Handed to the caller inside _BoundedReader, which closes it.
        raw = open(locator, "rb")  # noqa: SIM115
        raw.seek(start)
        return cast("BinaryIO", _BoundedReader(raw, end_incl - start + 1))

    def write_stream(self, locator: str, reader) -> None:
        import os

        dest = Path(locator)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # "xb": a name collision is a bug in the caller, never an overwrite.
        with open(dest, "xb") as out:
            shutil.copyfileobj(reader, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        # The rename-free swap commits a row pointing at this file; make the
        # directory entry durable too before that can happen.
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def sibling_locator(self, locator: str, name: str) -> str:
        return str(Path(locator).with_name(name))


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

    def open_range(self, locator: str, start: int, end_incl: int) -> BinaryIO:
        obj = self._s3.get_object(Bucket=self._bucket, Key=locator, Range=f"bytes={start}-{end_incl}")
        return cast("BinaryIO", obj["Body"])

    def write_stream(self, locator: str, reader) -> None:
        from boto3.s3.transfer import TransferConfig

        # Streams the reader in 16 MiB parts: no local temp copy of the object.
        # S3 offers no create-if-absent here; locators passed to this are
        # unique by construction (a random suffix per attempt).
        self._s3.upload_fileobj(
            reader,
            self._bucket,
            locator,
            Config=TransferConfig(multipart_chunksize=16 * 1024 * 1024, max_concurrency=4),
        )

    def sibling_locator(self, locator: str, name: str) -> str:
        head, sep, _tail = locator.rpartition("/")
        return f"{head}{sep}{name}"


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


class _EncryptedFileResponse(FileResponse):
    """The plaintext of an ENCRYPTED stored file, served with FileResponse's own
    semantics.

    Everything about the response - Range parsing, If-Range, 416 and 400,
    multi-range, HEAD, Content-Disposition, Accept-Ranges - is FileResponse's,
    inherited unchanged; only the three methods that read the file are replaced
    to stream decrypted plaintext of exactly the requested window. That is what
    keeps the desktop client's `bytes=1-1` probe (206 + Content-Range + ETag),
    its segmented downloads and a browser's resume working the same as for a
    plaintext file. The ETag and Last-Modified come from the row, not the
    ciphertext's stat, so they are stable across a re-encryption. `pathsend`
    is never used: it would hand the server the CIPHERTEXT path.
    """

    chunk_size = 1024 * 1024

    def __init__(
        self,
        *,
        backend: StorageBackend,
        locator: str,
        cipher: StoredCipher,
        filename: str,
        media_type: str,
        disposition: str,
        extra_headers: dict[str, str] | None,
        dl_id: str | None,
        ref: str,
    ) -> None:
        import os
        import stat
        from email.utils import formatdate

        from .file_encryption import opener

        mtime = (
            cipher.last_modified.replace(tzinfo=timezone.utc).timestamp()
            if cipher.last_modified is not None
            else 0
        )
        headers = dict(extra_headers or {})
        headers["etag"] = cipher.etag
        headers["last-modified"] = formatdate(mtime, usegmt=True)
        st = os.stat_result(
            (stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, cipher.plaintext_size, int(mtime), int(mtime), int(mtime))
        )
        super().__init__(
            path=locator,
            headers=headers,
            media_type=media_type,
            filename=filename,
            stat_result=st,
            content_disposition_type=disposition,
        )
        self._opener = opener(backend, locator)
        self._cipher = cipher
        self._dl_id = dl_id
        self._ref = ref

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._dl_id is not None:
                from . import transfer_activity

                transfer_activity.download_finished(self._dl_id)

    async def _body(self, send, start: int, end: int, *, more_after: bool) -> None:
        """Send plaintext [start, end) as body messages."""
        from starlette.concurrency import iterate_in_threadpool

        from ..utils import file_crypto

        if end <= start:
            if not more_after:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        pieces = file_crypto.iter_plaintext(
            self._opener, self._cipher.dek, self._cipher.plaintext_size, start, end - 1
        )
        try:
            async for piece in iterate_in_threadpool(pieces):
                await send({"type": "http.response.body", "body": piece, "more_body": True})
        except file_crypto.FileCryptoError as e:
            # Headers are out: the only honest thing left is to abort the
            # connection, so the client sees a failed transfer, not a file.
            import anyio

            await anyio.to_thread.run_sync(_report_integrity_failure, self._ref, e)
            raise
        if not more_after:
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_simple(self, send, send_header_only: bool, send_pathsend: bool) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await self._body(send, 0, self._cipher.plaintext_size, more_after=False)

    async def _handle_single_range(self, send, start: int, end: int, file_size: int, send_header_only: bool) -> None:
        from starlette.datastructures import MutableHeaders

        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await self._body(send, start, end, more_after=False)

    async def _handle_multiple_ranges(self, send, ranges, file_size: int, send_header_only: bool) -> None:
        from secrets import token_hex

        from starlette.datastructures import MutableHeaders

        boundary = token_hex(13)
        content_length, header_generator = self.generate_multipart(
            ranges, boundary, file_size, self.headers["content-type"]
        )
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(content_length)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send({"type": "http.response.body", "body": header_generator(start, end), "more_body": True})
            await self._body(send, start, end, more_after=True)
            await send({"type": "http.response.body", "body": b"\r\n", "more_body": True})
        await send({"type": "http.response.body", "body": f"--{boundary}--".encode("latin-1"), "more_body": False})


def _report_integrity_failure(ref: str, exc: Exception) -> None:
    """An encrypted file failed to authenticate WHILE being served. Audited
    and sent to the error log/alerts once an hour per file; never raises."""
    logger.error("encrypted file %s failed authentication while served: %s", ref, exc)
    try:
        from . import alert_dedup

        if alert_dedup.seen_recently(f"fh:enc:integrity:{ref}", 3600):
            return
        from ..database import SessionLocal
        from ..models.audit_log import AuditEventType
        from . import job_queue
        from .audit import record_audit_event

        db = SessionLocal()
        try:
            record_audit_event(
                db,
                event_type=AuditEventType.file_integrity_failed,
                actor_user_id=None,
                target_type="file",
                target_id=ref,
                metadata={"error": str(exc)[:200]},
            )
            db.commit()
        finally:
            db.close()
        job_queue.enqueue(
            "notify_admin_error",
            event={
                "source": "http",
                "exception_type": type(exc).__name__,
                "message": f"stored file {ref} failed authentication: {exc}"[:500],
                "method": "GET",
                "path": f"file:{ref}",
                "status_code": 500,
                "code": "FILE_INTEGRITY_FAILED",
                "request_id": None,
                "user_id": None,
                "auth_via": None,
                "at": utc_now().isoformat(),
            },
        )
    except Exception:
        logger.warning("integrity-failure report failed for %s", ref, exc_info=True)


def serve_response(
    backend: StorageBackend,
    *,
    locator: str,
    filename: str,
    mime_type: str,
    ttl_sec: int,
    cipher: StoredCipher | None,
    disposition: str = "attachment",
    extra_headers: dict[str, str] | None = None,
    count: bool = False,
    file_id: str | None = None,
):
    """The HTTP response for a file. Local backend → FileResponse (kernel
    sendfile, Range-capable); object backend → 307 redirect to a presigned URL
    so the browser fetches/resumes bytes from the store directly.

    `cipher` is REQUIRED (services/file_encryption.cipher_for_file /
    cipher_for_attachment, or None for bytes that are never encrypted, like the
    logo): every caller has to decide, and mypy holds it to that. None takes
    exactly the path described here. An encrypted file is decrypted and
    streamed by this process on BOTH backends - a presigned URL would hand the
    browser ciphertext - with FileResponse's own Range semantics
    (_EncryptedFileResponse), and is counted for the drain on both.

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

    if cipher is not None:
        from ..middleware.errors import AppError
        from ..utils import file_crypto

        # Checked before the 200 goes out: a ciphertext of the wrong length can
        # only fail mid-stream, after the client was promised a whole file.
        try:
            stored = backend.size(locator)
        except Exception as e:
            logger.error("encrypted file %s: cannot stat %s: %s", file_id, locator, e)
            raise AppError(500, "FILE_UNREADABLE", "This file cannot be read.") from e
        if stored != file_crypto.ciphertext_size(cipher.plaintext_size):
            logger.error(
                "encrypted file %s: %d stored bytes, expected %d",
                file_id, stored, file_crypto.ciphertext_size(cipher.plaintext_size),
            )
            raise AppError(500, "FILE_UNREADABLE", "This file cannot be read.")
        enc_dl_id = None
        if count:
            from . import transfer_activity

            enc_dl_id = transfer_activity.download_started(file_id)
        return _EncryptedFileResponse(
            backend=backend,
            locator=locator,
            cipher=cipher,
            filename=filename,
            media_type=mime_type,
            disposition=disposition,
            extra_headers=extra_headers,
            dl_id=enc_dl_id,
            ref=file_id or locator,
        )

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
