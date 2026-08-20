"""Daily cron: unlink quarantined-file bytes after a retention window.

CLAUDE.md's quarantine flow keeps infected files on disk under
`QUARANTINE_DIR` indefinitely so admins can release / inspect / purge
via the admin UI. In practice many incidents don't get touched and
the bytes accumulate.

This cron walks `files` rows in `state=infected` that were QUARANTINED longer
ago than `QUARANTINE_PURGE_AFTER_DAYS`, unlinks the bytes, and
leaves the row at `state=infected` with `storage_path=None` as a
historical marker - same semantics as the existing admin "purge"
action in services/quarantine_admin.py.

Set `QUARANTINE_PURGE_AFTER_DAYS=0` to disable.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func

from ..database import SessionLocal
from ..models.audit_log import AuditEventType, AuditLog
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
        # Age off the QUARANTINE time, not `finalized_at`.
        #
        # `finalized_at` is when the file was uploaded. Both the tunable and the
        # docstring say "quarantined longer than", and for anything that sat
        # clean for a while before a signature update caught it those are
        # different dates - so a file quarantined yesterday from a six-month-old
        # upload was purged on its first nightly run, destroying the evidence
        # before an admin had a working day to look at it. The
        # `file_quarantined` audit row already carries the real timestamp, so
        # this needs no new column (audit 2026-07-30). Files with no such row
        # (quarantined before that event existed) fall back to `finalized_at`,
        # which is the old behaviour and cannot be worse than it.
        quarantined_at = (
            db.query(
                AuditLog.target_id.label("file_id"),
                func.max(AuditLog.created_at).label("at"),
            )
            .filter(AuditLog.event_type == AuditEventType.file_quarantined.value)
            .group_by(AuditLog.target_id)
            .subquery()
        )
        rows = (
            db.query(File)
            .outerjoin(quarantined_at, quarantined_at.c.file_id == File.id)
            .filter(
                File.state == FileState.infected,
                File.storage_path.is_not(None),
                func.coalesce(quarantined_at.c.at, File.finalized_at).is_not(None),
                func.coalesce(quarantined_at.c.at, File.finalized_at) < cutoff,
            )
            .all()
        )
        from ..services.storage_backend import get_storage_backend
        backend = get_storage_backend()
        for f in rows:
            loc = f.storage_path
            if loc is None:  # the query filters these out; keeps the type honest
                continue
            try:
                backend.delete(loc)
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
