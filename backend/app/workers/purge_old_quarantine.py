"""Daily cron: unlink quarantined-file bytes after a retention window.

CLAUDE.md's quarantine flow keeps infected files on disk under
`QUARANTINE_DIR` indefinitely so admins can release / inspect / purge
via the admin UI. In practice many incidents don't get touched and
the bytes accumulate.

This cron walks `files` rows in `state=infected` whose `finalized_at`
is older than `QUARANTINE_PURGE_AFTER_DAYS`, unlinks the bytes, and
leaves the row at `state=infected` with `storage_path=None` as a
historical marker - same semantics as the existing admin "purge"
action in services/quarantine_admin.py.

Set `QUARANTINE_PURGE_AFTER_DAYS=0` to disable.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.purge_old_quarantine")




@track_cron("purge_old_quarantine")
async def purge_old_quarantine(_ctx) -> dict:
    purged = 0
    failed = 0
    db = SessionLocal()
    try:
        from ..services import settings_registry
        days = settings_registry.effective(
            db, settings_registry.K.QUARANTINE_PURGE_AFTER_DAYS
        )
        if days <= 0:
            return {"disabled": True, "purged": 0}
        cutoff = utc_now() - timedelta(days=days)
        rows = (
            db.query(File)
            .filter(
                File.state == FileState.infected,
                File.finalized_at.is_not(None),
                File.finalized_at < cutoff,
                File.storage_path.is_not(None),
            )
            .all()
        )
        from ..services.storage_backend import get_storage_backend
        backend = get_storage_backend()
        for f in rows:
            try:
                backend.delete(f.storage_path)
                f.storage_path = None
                record_audit_event(
                    db,
                    event_type=AuditEventType.file_quarantine_purged,
                    actor_user_id=None,
                    target_type="file",
                    target_id=f.id,
                    metadata={
                        "reason": "retention_window_exceeded",
                        "age_days": days,
                    },
                )
                db.commit()
                purged += 1
            except Exception as e:
                db.rollback()
                failed += 1
                logger.exception(
                    "purge_old_quarantine: failed file=%s path=%s: %s",
                    f.id, f.storage_path, e,
                )

        if purged or failed:
            logger.info(
                "purge_old_quarantine: purged=%d failed=%d (cutoff=%dd)",
                purged, failed, days,
            )
        return {"purged": purged, "failed": failed, "cutoff_days": days}
    finally:
        db.close()
