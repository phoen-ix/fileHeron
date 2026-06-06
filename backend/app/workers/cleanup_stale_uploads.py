"""Hourly job: reap uploads abandoned mid-stream.

A `files` row is created in `state=uploading` at upload init, then flips to
`ready_unscanned` once the bytes land (TUS post-finish hook or the direct
endpoint). If the client never finishes - tab closed, network drop, a failed
or cancelled transfer - the row sits in `uploading` forever, and because the
sent folder lists by *share* state the parent share shows as a perpetual
upload.

`cleanup_abandoned_uploads` only sweeps tusd's disk working dir and deliberately
skips uploads whose row is still `uploading`, so nothing here overlaps. This
job is the DB-side reaper: it finds `files` rows stuck in `uploading` past
`retention.upload_stale_hours`, deletes any partial bytes, marks the file
`deleted`, and - when the share has no usable file left - flips the share to
`failed` so it drops out of the active sent folder.

Quota: we intentionally do NOT release_bytes here. Some abandoned rows never
reserved any (no tus_upload_id / no bytes on disk), so releasing would
over-credit; the hourly `quota_reconcile` recomputes used-bytes from disk and
is the single source of truth for drift.

Idempotent: a second run finds nothing (rows are `deleted`, shares out of
`active`). Per-file failures don't abort the batch.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from ..config import settings
from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.cleanup_stale_uploads")

# A share stays active while any file is still in flight or usable.
_USABLE_FILE_STATES = {
    FileState.uploading,
    FileState.ready_unscanned,
    FileState.clean,
}




def _unlink_partial_bytes(file: File) -> None:
    """Best-effort removal of any remnants of an abandoned upload."""
    # Finalized bytes (if any) live in the storage backend.
    if file.storage_path:
        from ..services.storage_backend import get_storage_backend
        try:
            get_storage_backend().delete(file.storage_path)
        except Exception as e:
            logger.warning(
                "cleanup_stale_uploads: storage delete failed file=%s: %s", file.id, e
            )
    # The tusd working files always stage on the local disk regardless of backend.
    if file.tus_upload_id:
        base = Path(settings.TUS_UPLOAD_DIR)
        for p in (base / file.tus_upload_id, base / f"{file.tus_upload_id}.info"):
            try:
                if p.is_file():
                    p.unlink()
            except OSError as e:
                logger.warning(
                    "cleanup_stale_uploads: unlink failed file=%s path=%s: %s",
                    file.id, p, e,
                )


@track_cron("cleanup_stale_uploads")
async def cleanup_stale_uploads(_ctx) -> dict:
    db = SessionLocal()
    files_reaped = 0
    shares_failed = 0
    scanned = 0
    try:
        from ..services import settings_registry

        hours = settings_registry.effective(
            db, settings_registry.K.UPLOAD_STALE_AFTER_HOURS
        )
        cutoff = utc_now() - timedelta(hours=hours)

        stale = (
            db.query(File)
            .filter(File.state == FileState.uploading, File.created_at < cutoff)
            .all()
        )
        scanned = len(stale)

        # Group by share so we evaluate each share's "any usable file left?"
        # once, after reaping its stale files.
        share_ids = {f.share_id for f in stale}

        for f in stale:
            _unlink_partial_bytes(f)
            f.state = FileState.deleted
            record_audit_event(
                db,
                event_type=AuditEventType.file_upload_abandoned,
                actor_user_id=None,
                target_type="file",
                target_id=f.id,
                metadata={
                    "size_bytes": f.size_bytes,
                    "filename": f.original_filename,
                    "share_id": f.share_id,
                },
            )
            files_reaped += 1

        db.flush()

        now = utc_now()
        for share_id in share_ids:
            share = db.query(Share).filter(Share.id == share_id).first()
            if share is None or share.state != ShareState.active:
                continue
            has_usable = any(fl.state in _USABLE_FILE_STATES for fl in share.files)
            if has_usable:
                continue
            share.state = ShareState.failed
            share.terminated_at = now
            record_audit_event(
                db,
                event_type=AuditEventType.share_failed,
                actor_user_id=None,
                target_type="share",
                target_id=share.id,
                metadata={"reason": "upload_abandoned"},
            )
            shares_failed += 1

        db.commit()
        if files_reaped or shares_failed:
            logger.info(
                "cleanup_stale_uploads: scanned=%d files_reaped=%d shares_failed=%d",
                scanned, files_reaped, shares_failed,
            )
        return {
            "scanned": scanned,
            "files_reaped": files_reaped,
            "shares_failed": shares_failed,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
