"""File records — create / finalize / delete.

Storage layout: ``{STORAGE_ROOT}/{yyyy}/{mm}/{file_id}.bin``
                e.g. /data/files/2026/05/abc-123-...-def.bin

Finalize uses ``shutil.move`` so it works whether STORAGE_ROOT and
TUS_UPLOAD_DIR are on the same filesystem (atomic rename) or not
(fall back to copy + unlink). Docker bind mounts often appear as
distinct filesystems inside the container, hence the fallback.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.share import Share
from ..models.user import User
from .audit import record_audit_event
from .quota import release_bytes

logger = logging.getLogger("fileheron.file")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def storage_path_for(file_id: str, when: datetime | None = None) -> Path:
    """Compute the deterministic on-disk path for a finalized file."""
    when = when or _utcnow()
    return Path(settings.STORAGE_ROOT) / f"{when.year:04d}" / f"{when.month:02d}" / f"{file_id}.bin"


def create_pending(
    db: Session,
    *,
    share: Share,
    uploader: User,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
) -> File:
    """Insert a `files` row in state=uploading. Returns the row (caller
    commits).
    """
    record = File(
        share_id=share.id,
        original_filename=original_filename[:512],
        mime_type=(mime_type or "application/octet-stream")[:255],
        size_bytes=size_bytes,
        state=FileState.uploading,
        uploaded_by_id=uploader.id,
    )
    db.add(record)
    db.flush()
    return record


def finalize_to_disk(
    db: Session, *, file: File, tus_upload_id: str, request=None
) -> Path:
    """Move the tusd working file into permanent storage. Update the row's
    state, storage_path, finalized_at, and clear tus_upload_id.

    The tusd working file is at ``{TUS_UPLOAD_DIR}/{tus_upload_id}``. (No
    extension; tusd uses raw binary names.)

    Side effects:
    - Creates target subdirectories if needed.
    - Atomic rename when TUS_UPLOAD_DIR and STORAGE_ROOT share a
      filesystem; otherwise shutil.move falls back to copy + unlink
      (Docker bind mounts often look cross-device inside the
      container even on the same host disk — Errno 18 EXDEV).
    """
    src = Path(settings.TUS_UPLOAD_DIR) / tus_upload_id
    if not src.is_file():
        # tusd may store metadata as `<id>.info`. The data file is just <id>.
        # If neither exists, the upload didn't actually land — bail out.
        raise AppError(500, "UPLOAD_MISSING", f"tusd upload {tus_upload_id} not found on disk.")

    when = _utcnow()
    dest = storage_path_for(file.id, when)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # shutil.move == os.rename if same fs, else copy2 + unlink.
    shutil.move(str(src), str(dest))

    # tusd also writes a sidecar .info — clean it up.
    info_sidecar = src.with_suffix(".info")
    if info_sidecar.is_file():
        try:
            info_sidecar.unlink()
        except OSError:
            pass

    file.storage_path = str(dest)
    file.finalized_at = when
    file.state = FileState.ready_unscanned
    file.tus_upload_id = None
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.file_finalized,
        actor_user_id=file.uploaded_by_id,
        target_type="file",
        target_id=file.id,
        metadata={"size_bytes": file.size_bytes, "filename": file.original_filename},
        request=request,
    )
    logger.info("file finalized id=%s path=%s size=%d", file.id, dest, file.size_bytes)
    return dest


def hard_delete(
    db: Session, *, file: File, reason: str = "user_request", request=None
) -> None:
    """Hard-delete from disk + DB row marker."""
    # Capture the pre-delete state so we don't double-release quota for
    # files that already went through quarantine (services/quarantine.py
    # released the bytes when it moved the file into ./data/quarantine/).
    was_infected = file.state == FileState.infected

    if file.storage_path:
        path = Path(file.storage_path)
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.warning("could not unlink %s: %s", path, e)

    file.state = FileState.deleted
    db.flush()

    if not was_infected:
        release_bytes(user_id=file.uploaded_by_id, bytes_to_free=file.size_bytes)

    record_audit_event(
        db,
        event_type=AuditEventType.file_deleted,
        actor_user_id=file.uploaded_by_id,
        target_type="file",
        target_id=file.id,
        metadata={"reason": reason, "size_bytes": file.size_bytes},
        request=request,
    )


def delete_file_for_expiry(db: Session, *, file: File) -> None:
    """Cleanup-worker variant: hard-delete with `file_expired` audit event,
    no actor (system action). Idempotent — silently no-ops if the row is
    already deleted or the file is missing on disk."""
    if file.state == FileState.deleted:
        return
    if file.storage_path:
        path = Path(file.storage_path)
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.warning("could not unlink %s: %s", path, e)

    file.state = FileState.deleted
    db.flush()

    release_bytes(user_id=file.uploaded_by_id, bytes_to_free=file.size_bytes)

    record_audit_event(
        db,
        event_type=AuditEventType.file_expired,
        actor_user_id=None,
        target_type="file",
        target_id=file.id,
        metadata={"size_bytes": file.size_bytes, "filename": file.original_filename},
    )
