"""Unsubscribe footer injection + List-Unsubscribe header + always-on locking."""
from __future__ import annotations

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from app.services import email as email_svc
from app.services import notification as notif_svc
from app.services import notification_prefs


def _share_payload(user):
    return {
        "sender_name": "Alice",
        "subject": "Hello",
        "message": None,
        "expires_at": None,
        "file_count": 1,
        "share_url": "https://example.com/x",
        "recipient_name": user.display_name,
    }


def test_render_email_injects_footer_for_known_recipient(make_user, db):
    user = make_user(email="u@test.local")
    subject, text, html = email_svc.render_email(
        "en", "share_created", _share_payload(user),
        app_url="https://fh.example",
        recipient_user_id=user.id,
        category="share_created",
    )
    assert "Manage subscriptions:" in text
    assert "https://fh.example/manage-notifications/" in text
    assert "?off=share_created" in text  # opt-outable -> unsubscribe link present
    assert html and "Manage subscriptions" in html


def test_render_email_no_footer_without_recipient(make_user):
    user = make_user(email="u@test.local")
    _subject, text, _html = email_svc.render_email(
        "en", "share_created", _share_payload(user), app_url="https://fh.example",
    )
    assert "Manage subscriptions" not in text


def test_locked_category_has_manage_but_no_unsubscribe(make_user):
    user = make_user(email="u@test.local")
    _s, text, _h = email_svc.render_email(
        "en", "login_alert",
        {"display_name": user.display_name, "at": None, "via": "password",
         "ua_summary": "x", "ip_hint": "1.2.3.0"},
        app_url="https://fh.example",
        recipient_user_id=user.id,
        category="login_alert",
    )
    assert "Manage subscriptions:" in text
    assert "?off=" not in text  # locked -> no per-type unsubscribe


def test_list_unsubscribe_header_for_optoutable():
    val = email_svc.list_unsubscribe_header(3, "share_created", "https://fh.example")
    assert val is not None
    assert "/api/notification-subscriptions/" in val
    assert "one-click?category=share_created" in val


def test_list_unsubscribe_header_none_for_locked():
    assert email_svc.list_unsubscribe_header(3, "login_alert", "https://fh.example") is None
    # auth slugs aren't even in the enum -> also None.
    assert email_svc.list_unsubscribe_header(3, "reset_password", "https://fh.example") is None


def test_dispatch_enqueues_list_unsubscribe(make_user, db, monkeypatch):
    user = make_user(email="u@test.local", role=UserRole.client)
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *a, **kw: enqueued.append((a, kw)),
    )
    notif_svc.dispatch(
        db, user=user, category=NotificationCategory.share_created,
        payload=_share_payload(user), email_to="bob@example.com",
    )
    db.commit()
    job = next(kw for a, kw in enqueued if a == ("send_email_job",))
    assert job["list_unsubscribe"] is not None
    assert "Manage subscriptions:" in job["text_body"]


def test_effective_channel_overrides_stored_off_for_locked(make_user, db):
    """A user who turned login_alert off must still get it (always-on)."""
    user = make_user(email="u@test.local")
    db.add(
        UserNotificationPreference(
            user_id=user.id,
            category=NotificationCategory.login_alert,
            channel=NotificationChannel.off,
        )
    )
    db.commit()
    ch = notification_prefs.effective_channel(db, user.id, NotificationCategory.login_alert)
    # Default for login_alert is email; the stored `off` is ignored.
    assert ch == NotificationChannel.email


def test_dispatch_still_emails_locked_when_user_opted_off(make_user, db, monkeypatch):
    user = make_user(email="u@test.local")
    db.add(
        UserNotificationPreference(
            user_id=user.id,
            category=NotificationCategory.login_alert,
            channel=NotificationChannel.off,
        )
    )
    db.commit()
    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *a, **kw: enqueued.append((a, kw)),
    )
    notif_svc.dispatch(
        db, user=user, category=NotificationCategory.login_alert,
        payload={"display_name": user.display_name, "at": None, "via": "password",
                 "ua_summary": "x", "ip_hint": "1.2.3.0"},
        email_to="u@test.local",
    )
    db.commit()
    rows = db.query(Notification).filter(Notification.user_id == user.id).all()
    assert len(rows) == 1  # still recorded + emailed
    assert any(a == ("send_email_job",) for a, _kw in enqueued)
