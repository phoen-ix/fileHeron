"""cleanup_read_notifications worker — age-out in-app notifications by
``created_at`` (read state retired in v1.6.1); recent ones are kept, and the
``days <= 0`` setting disables it. Read state no longer matters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.workers.cleanup_read_notifications import cleanup_read_notifications


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _notif(user_id, *, created_at, read_at=None):
    return Notification(
        user_id=user_id,
        category=NotificationCategory.share_created,
        payload_json={},
        created_at=created_at,
        read_at=read_at,
    )


@pytest.mark.asyncio
async def test_deletes_old_notifications_by_age(make_user, db, monkeypatch):
    from app.config import settings as cfg

    user = make_user(email="n@test.local", role=UserRole.client)
    now = _now()
    retention = cfg.NOTIFICATION_READ_RETENTION_DAYS  # default 3

    old = _notif(user.id, created_at=now - timedelta(days=retention + 2))
    # Old AND previously-read → still deleted (age-based, read-independent).
    old_read = _notif(
        user.id,
        created_at=now - timedelta(days=retention + 2),
        read_at=now - timedelta(days=1),
    )
    recent = _notif(user.id, created_at=now - timedelta(hours=1))
    db.add_all([old, old_read, recent])
    db.commit()
    old_id, old_read_id, recent_id = old.id, old_read.id, recent.id

    from app.workers import cleanup_read_notifications as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    result = await cleanup_read_notifications(None)
    assert result["deleted"] == 2

    assert db.query(Notification).filter(Notification.id == old_id).count() == 0
    assert db.query(Notification).filter(Notification.id == old_read_id).count() == 0
    assert db.query(Notification).filter(Notification.id == recent_id).count() == 1


@pytest.mark.asyncio
async def test_disabled_when_retention_zero(make_user, db, monkeypatch):
    from app.config import settings as cfg

    user = make_user(email="n2@test.local", role=UserRole.client)
    db.add(_notif(user.id, created_at=_now() - timedelta(days=999)))
    db.commit()

    monkeypatch.setattr(cfg, "NOTIFICATION_READ_RETENTION_DAYS", 0)
    from app.workers import cleanup_read_notifications as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    result = await cleanup_read_notifications(None)
    assert result["deleted"] == 0
    assert db.query(Notification).filter(Notification.user_id == user.id).count() == 1
