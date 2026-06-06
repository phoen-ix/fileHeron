"""Daily cron: reclaim orphaned file bytes after a grace window.

Revoking a share is a soft state-flip (services/share.py::revoke_share) - it
keeps the file bytes as a recovery margin, unlike expiry which hard-deletes
them. So a revoked (or deleted) share's files linger on disk in state
``clean``/``ready_unscanned``, still counting the uploader's quota, invisible
in the default active-only Sent/Received view. These are "orphans".

This cron frees them once their share has been terminal for
``ORPHAN_RECLAIM_AFTER_DAYS`` (admin-tunable; 0 disables auto-reclaim - admins
can still reclaim manually from /admin/file-history). It reuses the canonical
``services/file.py::hard_delete`` helper (unlink + state=deleted + release
quota + audit) and notifies admins of what it freed.

Orphan filter (deliberately excludes quarantine, which is ``infected`` with
bytes in QUARANTINE_DIR):
    File.state IN (clean, ready_unscanned) AND File.storage_path IS NOT NULL
    AND parent Share.state IN (revoked, deleted)

Grace is aged off ``shares.terminated_at``. Terminal shares missing that stamp
(e.g. siblings of a quarantine-revoked file, or legacy rows) are stamped on
first sighting so they get a full grace window from now rather than being
reclaimed instantly.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import update

from ..database import SessionLocal
from ..models.file import File, FileState
from ..models.notification import NotificationCategory
from ..models.share import Share, ShareState
from ..models.user import User, UserRole
from ..services.cron_tracker import track_cron
from ..services.notification import dispatch
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.reclaim_orphaned_files")

_TERMINAL = [ShareState.revoked, ShareState.deleted]
_RECLAIMABLE = [FileState.clean, FileState.ready_unscanned]




def _notify_admins(db, *, count: int, bytes_freed: int, days: int) -> None:
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    mb = bytes_freed / 1_000_000
    payload = {
        "reason": "orphaned_files_reclaimed",
        "detail": (
            f"Reclaimed {count} orphaned file(s) ({mb:.1f} MB) from shares "
            f"revoked/deleted at least {days} day(s) ago."
        ),
        "at": utc_now().isoformat(),
    }
    for admin in admins:
        try:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/file-history",
                email_to=admin.email,
            )
        except Exception:
            logger.exception("orphan-reclaim alert dispatch failed admin=%d", admin.id)


@track_cron("reclaim_orphaned_files")
async def reclaim_orphaned_files(_ctx) -> dict:
    db = SessionLocal()
    try:
        from ..services import file as file_svc
        from ..services import settings_registry

        days = settings_registry.effective(db, settings_registry.K.ORPHAN_RECLAIM_AFTER_DAYS)
        if days <= 0:
            return {"disabled": True, "reclaimed": 0}

        # Defensive: stamp any terminal share missing terminated_at so its
        # grace window starts now (not "immediately reclaim").
        db.execute(
            update(Share)
            .where(Share.state.in_(_TERMINAL), Share.terminated_at.is_(None))
            .values(terminated_at=utc_now())
        )
        db.commit()

        cutoff = utc_now() - timedelta(days=days)
        orphans = (
            db.query(File)
            .join(Share, File.share_id == Share.id)
            .filter(
                File.state.in_(_RECLAIMABLE),
                File.storage_path.is_not(None),
                Share.state.in_(_TERMINAL),
                Share.terminated_at.is_not(None),
                Share.terminated_at < cutoff,
            )
            .all()
        )

        reclaimed = 0
        failed = 0
        bytes_freed = 0
        for f in orphans:
            size = f.size_bytes
            try:
                file_svc.hard_delete(db, file=f, reason="orphan_reclaim")
                db.commit()
                reclaimed += 1
                bytes_freed += size
            except Exception as e:
                db.rollback()
                failed += 1
                logger.exception("reclaim_orphaned_files: failed file=%s: %s", f.id, e)

        if reclaimed:
            _notify_admins(db, count=reclaimed, bytes_freed=bytes_freed, days=days)
            db.commit()
            logger.info(
                "reclaim_orphaned_files: reclaimed=%d bytes=%d failed=%d (grace=%dd)",
                reclaimed, bytes_freed, failed, days,
            )
        return {"reclaimed": reclaimed, "bytes_freed": bytes_freed, "failed": failed, "grace_days": days}
    finally:
        db.close()
