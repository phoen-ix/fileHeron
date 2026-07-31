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
from ..models.share import Share, ShareState
from ..services import file as file_svc
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
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
            # Phase 1: mark rows + audit inside the transaction. Phase 2 (the
            # irreversible byte unlink + the non-transactional Redis quota
            # release) runs only after the per-share commit succeeds, so a
            # commit failure can neither lose a live file nor double-release
            # quota (audit M14). Shared with the owner-driven expire paths since
            # the 2026-07-30 audit - they had drifted back to purge-then-commit.
            to_purge: list[file_svc.PurgeEntry] = []
            file_count = 0
            for f in share.files:
                entry = file_svc.mark_deleted_for_expiry(db, file=f)
                if entry is None:
                    continue
                to_purge.append(entry)
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
            deleted_files += file_svc.purge_expired_bytes(
                db, to_purge, reason="expire_purge_failed"
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
