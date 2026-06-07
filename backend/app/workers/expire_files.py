"""Hourly job: expire shares whose expires_at has passed.

For each expired share that's still `active`:
- transition state → `expired`
- hard-delete every file on disk
- emit audit_log(file_expired) per file + audit_log(share_expired) per share

Idempotent: re-running the job picks up no work the second time because
shares move out of `active` after the first run. File deletion is also
idempotent (silently no-ops on missing files).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import selectinload

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import FileState
from ..models.share import Share, ShareState
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..services.quota import release_bytes
from ..services.storage_backend import get_storage_backend
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.expire_files")




@track_cron("expire_files")
async def expire_files(_ctx) -> dict:
    """Walk shares.expires_at < now, transition state + hard-delete files.

    Per-share commit so a single bad share (e.g., disk unlink failure on
    one file) doesn't poison the rest of the batch."""
    db = SessionLocal()
    expired_shares = 0
    deleted_files = 0
    failed_shares = 0
    try:
        now = utc_now()
        shares = (
            db.query(Share)
            .options(selectinload(Share.files))
            .filter(Share.state == ShareState.active, Share.expires_at < now)
            .all()
        )
        for share in shares:
            # (locator, user_id, size) to purge AFTER the commit succeeds.
            to_purge: list[tuple[str | None, int, int]] = []
            file_count = 0
            for f in share.files:
                if f.state == FileState.deleted:
                    continue
                # Mark deleted + audit now, but DON'T unlink bytes or release
                # quota yet. The irreversible byte delete + non-transactional
                # Redis release happen only AFTER the per-share commit (below),
                # so a commit failure can't leave a still-'clean' row whose
                # bytes are already gone (silent data loss) nor double-release
                # the quota on the next cron cycle (audit M14).
                to_purge.append((f.storage_path, f.uploaded_by_id, f.size_bytes))
                f.state = FileState.deleted
                record_audit_event(
                    db,
                    event_type=AuditEventType.file_expired,
                    actor_user_id=None,
                    target_type="file",
                    target_id=f.id,
                    metadata={"size_bytes": f.size_bytes, "filename": f.original_filename},
                )
                file_count += 1
            share.state = ShareState.expired
            record_audit_event(
                db,
                event_type=AuditEventType.share_expired,
                actor_user_id=None,
                target_type="share",
                target_id=share.id,
                metadata={"file_count": file_count},
            )
            try:
                db.commit()
            except Exception:
                db.rollback()
                failed_shares += 1
                logger.exception(
                    "expire_files: commit failed for share=%s", share.id
                )
                continue  # nothing purged yet -> safe to retry next run
            expired_shares += 1
            # Post-commit: irreversible byte unlink + best-effort quota release.
            # A failure here leaks bytes on disk (cleaned by orphan-reclaim /
            # disk sweep) but never loses a live file or double-releases quota.
            backend = get_storage_backend()
            for locator, uid, size in to_purge:
                if locator:
                    try:
                        backend.delete(locator)
                    except Exception as e:
                        logger.error(
                            "expire_files: post-commit byte purge failed share=%s: %s",
                            share.id, e,
                        )
                release_bytes(user_id=uid, bytes_to_free=size)
                deleted_files += 1
        if expired_shares or failed_shares:
            logger.info(
                "expire_files: expired %d shares (%d failed), deleted %d files",
                expired_shares, failed_shares, deleted_files,
            )
        return {
            "expired_shares": expired_shares,
            "deleted_files": deleted_files,
            "failed_shares": failed_shares,
        }
    finally:
        db.close()
