"""cleanup_read_notifications worker — hard-delete READ notifications older
than NOTIFICATION_READ_RETENTION_DAYS; leave unread + recently-read alone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.workers.cleanup_read_notifications import cleanup_read_notifications


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _notif(user_id, *, read_at):
    return Notification(
        user_id=user_id,
        category=NotificationCategory.share_created,
        payload_json={},
        read_at=read_at,
    )


@pytest.mark.asyncio
async def test_deletes_only_old_read_notifications(make_user, db, monkeypatch):
    from app.config import settings as cfg

    user = make_user(email="n@test.local", role=UserRole.client)
    now = _now()
    retention = cfg.NOTIFICATION_READ_RETENTION_DAYS  # default 3

    old_read = _notif(user.id, read_at=now - timedelta(days=retention + 2))
    recent_read = _notif(user.id, read_at=now - timedelta(hours=1))
    unread_old = _notif(user.id, read_at=None)
    db.add_all([old_read, recent_read, unread_old])
    db.commit()
    old_id, recent_id, unread_id = old_read.id, recent_read.id, unread_old.id

    from app.workers import cleanup_read_notifications as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    result = await cleanup_read_notifications(None)
    assert result["deleted"] == 1

    assert db.query(Notification).filter(Notification.id == old_id).count() == 0
    assert db.query(Notification).filter(Notification.id == recent_id).count() == 1
    assert db.query(Notification).filter(Notification.id == unread_id).count() == 1


@pytest.mark.asyncio
async def test_disabled_when_retention_zero(make_user, db, monkeypatch):
    from app.config import settings as cfg

    user = make_user(email="n2@test.local", role=UserRole.client)
    db.add(_notif(user.id, read_at=_now() - timedelta(days=999)))
    db.commit()

    monkeypatch.setattr(cfg, "NOTIFICATION_READ_RETENTION_DAYS", 0)
    from app.workers import cleanup_read_notifications as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    result = await cleanup_read_notifications(None)
    assert result["deleted"] == 0
    # Nothing deleted while disabled.
    assert db.query(Notification).filter(Notification.user_id == user.id).count() == 1
