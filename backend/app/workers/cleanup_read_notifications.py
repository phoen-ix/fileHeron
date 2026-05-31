"""Daily cron: prune READ in-app notifications past their retention window.

The notification bell shows only UNREAD items, so a read notification is
already invisible to the user — but the row lingers in `notifications` and
would otherwise accumulate forever. This cron hard-deletes notifications
whose ``read_at`` is older than ``NOTIFICATION_READ_RETENTION_DAYS``.

Deliberately NOT instant on mark-read: the row survives for the retention
window (default 30d) so support/debugging can still see recent history.
Set ``NOTIFICATION_READ_RETENTION_DAYS=0`` to disable.

Only READ notifications are pruned; unread ones are left alone regardless
of age (the user hasn't seen them yet). Idempotent — re-running matches no
new rows once a batch is gone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..database import SessionLocal
from ..models.notification import Notification
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.cleanup_read_notifications")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@track_cron("cleanup_read_notifications")
async def cleanup_read_notifications(_ctx) -> dict:
    """Hard-delete read notifications older than the retention window."""
    days = settings.NOTIFICATION_READ_RETENTION_DAYS
    if days <= 0:
        return {"deleted": 0, "skipped": "disabled"}

    db = SessionLocal()
    try:
        cutoff = _utcnow() - timedelta(days=days)
        deleted = (
            db.query(Notification)
            .filter(
                Notification.read_at.is_not(None),
                Notification.read_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("cleanup_read_notifications: deleted=%d (>%dd read)", deleted, days)
        return {"deleted": deleted}
    finally:
        db.close()
