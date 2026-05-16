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
from datetime import datetime, timezone

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.share import Share, ShareState
from ..services.audit import record_audit_event
from ..services.file import delete_file_for_expiry

logger = logging.getLogger("fileheron.workers.expire_files")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


async def expire_files(_ctx) -> dict:
    """Walk shares.expires_at < now, transition state + hard-delete files.

    Per-share commit so a single bad share (e.g., disk unlink failure on
    one file) doesn't poison the rest of the batch."""
    db = SessionLocal()
    expired_shares = 0
    deleted_files = 0
    failed_shares = 0
    try:
        now = _utcnow()
        shares = (
            db.query(Share)
            .filter(Share.state == ShareState.active, Share.expires_at < now)
            .all()
        )
        for share in shares:
            file_count = 0
            failed_files: list[str] = []
            for f in share.files:
                try:
                    delete_file_for_expiry(db, file=f)
                    file_count += 1
                    deleted_files += 1
                except OSError as e:
                    logger.error(
                        "expire_files: delete failed file=%s share=%s: %s",
                        f.id, share.id, e,
                    )
                    failed_files.append(f.id)
            share.state = ShareState.expired
            metadata: dict = {"file_count": file_count}
            if failed_files:
                metadata["failed_files"] = failed_files
            record_audit_event(
                db,
                event_type=AuditEventType.share_expired,
                actor_user_id=None,
                target_type="share",
                target_id=share.id,
                metadata=metadata,
            )
            try:
                db.commit()
                expired_shares += 1
            except Exception:
                db.rollback()
                failed_shares += 1
                logger.exception(
                    "expire_files: commit failed for share=%s", share.id
                )
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
