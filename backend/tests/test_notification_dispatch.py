"""Notification dispatch service - channel resolution + persistence."""
from __future__ import annotations

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from app.services import notification as notif_svc


def test_dispatch_writes_in_app_row_default(make_user, db, monkeypatch):
    """Default channel is `both`; the row should land in the table."""
    user = make_user(email="u@test.local", role=UserRole.client)
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )
    notif_svc.dispatch(
        db,
        user=user,
        category=NotificationCategory.share_created,
        payload={
            "sender_name": "Alice",
            "subject": "Hello",
            "message": None,
            "expires_at": None,
            "file_count": 1,
            "share_url": "https://example.com/x",
            "recipient_name": user.display_name,
        },
        email_to="bob@example.com",
    )
    db.commit()
    rows = db.query(Notification).filter(Notification.user_id == user.id).all()
    assert len(rows) == 1
    assert rows[0].category == NotificationCategory.share_created
    # `both` includes email - the worker job should have been enqueued too.
    assert any(args == ("send_email_job",) for args, _kwargs in enqueued)


def test_dispatch_off_writes_no_row_no_email(make_user, db, monkeypatch):
    user = make_user(email="u@test.local", role=UserRole.client)
    db.add(
        UserNotificationPreference(
            user_id=user.id,
            category=NotificationCategory.share_created,
            channel=NotificationChannel.off,
        )
    )
    db.commit()
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )
    notif_svc.dispatch(
        db,
        user=user,
        category=NotificationCategory.share_created,
        payload={
            "sender_name": "Alice",
            "subject": None,
            "message": None,
            "expires_at": None,
            "file_count": 1,
            "share_url": "https://example.com/x",
            "recipient_name": user.display_name,
        },
        email_to="bob@example.com",
    )
    db.commit()
    rows = db.query(Notification).filter(Notification.user_id == user.id).all()
    assert rows == []
    assert enqueued == []


def test_dispatch_in_app_only_skips_email(make_user, db, monkeypatch):
    user = make_user(email="u@test.local", role=UserRole.client)
    db.add(
        UserNotificationPreference(
            user_id=user.id,
            category=NotificationCategory.share_created,
            channel=NotificationChannel.in_app,
        )
    )
    db.commit()
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )
    notif_svc.dispatch(
        db,
        user=user,
        category=NotificationCategory.share_created,
        payload={
            "sender_name": "Alice",
            "subject": None,
            "message": None,
            "expires_at": None,
            "file_count": 1,
            "share_url": "https://example.com/x",
            "recipient_name": user.display_name,
        },
        email_to="bob@example.com",
    )
    db.commit()
    rows = db.query(Notification).filter(Notification.user_id == user.id).all()
    assert len(rows) == 1
    assert enqueued == []


def test_dispatch_skips_disabled_user(make_user, db, monkeypatch):
    user = make_user(email="u@test.local", role=UserRole.client, is_disabled=True)
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )
    result = notif_svc.dispatch(
        db,
        user=user,
        category=NotificationCategory.share_created,
        payload={
            "sender_name": "Alice",
            "subject": None,
            "message": None,
            "expires_at": None,
            "file_count": 1,
            "share_url": "https://example.com/x",
            "recipient_name": user.display_name,
        },
        email_to="bob@example.com",
    )
    db.commit()
    assert result is None
    assert enqueued == []


def test_dispatch_serializes_datetime_in_payload(make_user, db, monkeypatch):
    """The `expires_at` datetime must round-trip through the JSON column
    as an ISO string. Regression - without _json_safe, this raises."""
    from datetime import datetime, timezone

    user = make_user(email="u@test.local", role=UserRole.client)
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: None,
    )
    when = datetime(2026, 5, 5, 12, tzinfo=timezone.utc).replace(tzinfo=None)
    notif_svc.dispatch(
        db,
        user=user,
        category=NotificationCategory.share_created,
        payload={
            "sender_name": "Alice",
            "subject": "x",
            "message": None,
            "expires_at": when,
            "file_count": 1,
            "share_url": "https://example.com/x",
            "recipient_name": user.display_name,
        },
        email_to="bob@example.com",
    )
    db.commit()
    row = db.query(Notification).filter(Notification.user_id == user.id).one()
    assert row.payload_json["expires_at"].startswith("2026-05-05")
