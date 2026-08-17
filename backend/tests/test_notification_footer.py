"""Unsubscribe footer injection + List-Unsubscribe header + always-on locking."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
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


# --- the operational alerts are not one-tap disposable ------------------------
#
# ops_alert / server_error are the instance saying it is broken. They were
# ordinary opt-out categories, so every alert carried a List-Unsubscribe header
# with List-Unsubscribe-Post - Gmail and Outlook render an Unsubscribe button
# beside the sender, and one tap silently ends the alerting, on a deployment
# where one admin may be the only recipient with the email channel on.


@pytest.mark.parametrize("category", ["ops_alert", "server_error"])
def test_an_operational_alert_carries_no_one_click_unsubscribe(category, make_user):
    val = email_svc.list_unsubscribe_header(3, category, "https://fh.example")
    assert val is None, "a mail client would render a one-tap Unsubscribe"
    assert email_svc._is_unsubscribable(category) is False


def test_an_operational_alert_still_gets_the_manage_link(make_user):
    """Not reachable-but-refused: the recipient keeps a way to change it, it
    just isn't a single tap from the email."""
    user = make_user(email="admin@test.local", role=UserRole.admin)
    _s, text, _h = email_svc.render_email(
        "en", "ops_alert",
        {"reason": "cron_failed", "job_name": "release_check", "error": "x", "at": None},
        app_url="https://fh.example",
        recipient_user_id=user.id,
        category="ops_alert",
    )
    assert "Manage subscriptions:" in text
    assert "?off=" not in text


@pytest.mark.parametrize("category", ["ops_alert", "server_error"])
def test_the_endpoint_refuses_even_when_an_old_email_asks(category, make_user, db):
    """Mail already delivered carries a live ?off= and one-click URL for these,
    so the footer no longer emitting them is not the guard that holds."""
    user = make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as ei:
        notification_prefs.unsubscribe_category(db, user, category)
    assert ei.value.code == "NO_ONE_CLICK_UNSUBSCRIBE"


def test_an_operational_alert_is_still_switchable_deliberately(make_user, db):
    """The point of not using LOCKED_CATEGORIES: locked also means read-only and
    forces the default channel, which would DOWNGRADE an admin who chose `both`
    - turning the ops email off in the name of protecting it."""
    user = make_user(email="admin@test.local", role=UserRole.admin)
    notification_prefs.update_preferences(
        db, user, {"ops_alert": NotificationChannel.both.value}
    )
    db.commit()
    assert notification_prefs.effective_channel(
        db, user.id, NotificationCategory.ops_alert
    ) == NotificationChannel.both

    notification_prefs.update_preferences(
        db, user, {"ops_alert": NotificationChannel.off.value}
    )
    db.commit()
    assert notification_prefs.effective_channel(
        db, user.id, NotificationCategory.ops_alert
    ) == NotificationChannel.off


def test_the_prefs_row_marks_which_categories_one_click_can_reach(make_user, db):
    user = make_user(email="admin@test.local", role=UserRole.admin)
    by_cat = {r.category: r for r in notification_prefs.list_preferences(db, user)}
    assert by_cat[NotificationCategory.ops_alert].one_click is False
    assert by_cat[NotificationCategory.ops_alert].locked is False  # not read-only
    assert by_cat[NotificationCategory.share_created].one_click is True


def test_both_preference_routes_serialise_the_same_row(make_user, db):
    """Two routes hand-built this payload and a field added to one was absent
    from the other - and both flags decide whether an alert can be switched off."""
    import inspect

    from app.routers import notification_subscriptions, notifications

    for mod in (notification_subscriptions, notifications):
        src = inspect.getsource(mod)
        assert "PreferenceItem.from_row(" in src, f"{mod.__name__} builds it by hand"
        assert "PreferenceItem(category=" not in src


def test_the_one_click_post_header_value_is_the_one_rfc_8058_fixes():
    """RFC 8058 s3.1 fixes the value and clients match it literally. This read
    `List=One-Click`, which no client honours - so one-click silently degraded
    to the mailto fallback for every opt-outable category.

    Asserted on the header the builder actually emits, not on a re-derivation:
    the literal is written out here so the test fails if the constant moves."""
    from app.utils.emailing import build_message

    class _Cfg:
        from_header = "file:Heron <no-reply@example.com>"

    msg = build_message(
        _Cfg(),
        to="a@example.com",
        subject="s",
        text_body="t",
        list_unsubscribe="<https://fh.example/api/notification-subscriptions/tok/one-click?category=share_created>",
    )
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["List-Unsubscribe"].startswith("<https://")


def test_no_one_click_headers_when_there_is_nothing_to_unsubscribe_from():
    """An operational alert resolves `list_unsubscribe` to None, and the builder
    must then emit neither header - a bare List-Unsubscribe-Post would still
    make some clients offer the button."""
    from app.utils.emailing import build_message

    class _Cfg:
        from_header = "file:Heron <no-reply@example.com>"

    msg = build_message(_Cfg(), to="a@example.com", subject="s", text_body="t")
    assert msg["List-Unsubscribe"] is None
    assert msg["List-Unsubscribe-Post"] is None
