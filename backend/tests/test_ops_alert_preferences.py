"""An admin chooses, per alert type, where an operational alert reaches them.

`ops_alert` was ONE preference for sixteen different things - a failed
antivirus scan, a full disk, "an update started" - and it defaulted to the
bell only. So the alarm that ClamAV could not scan an upload would have been a
dot on a bell nobody was looking at (2026-11-20), and "mail me when something
is broken, not when an update starts" could not be said.

Now: one row per alert type (`admin_alerts.OPS_REASONS`), "something is
broken" defaulting to email + bell and routine notices to the bell, each
switchable on its own. A category-wide choice made before this stays the
fallback for every type without a row of its own.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.email_log import EmailLog
from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
    UserNotificationTopicPreference,
)
from app.services import admin_alerts, notification_prefs

ALARMS = {
    "av_scan_failed", "av_unhealthy", "storage_critical_low", "storage_unreadable",
    "cron_failed", "cron_failing", "redis_unhealthy", "smtp_failing", "update_failed",
}
# Things that happened, and the two that can arrive by the dozen under attack.
NOTICES = {
    "update_auto_scheduled", "update_triggered", "rollback_triggered", "update_completed",
    "orphaned_files_reclaimed", "anomaly", "scan_guard_block",
}


def _send(db, reason: str) -> None:
    admin_alerts.notify_admins(db, payload={"reason": reason, "detail": "x"})
    db.commit()


def _got(db, user_id: int) -> tuple[int, int]:
    """(bell rows, mails) this user received."""
    bell = db.query(Notification).filter(Notification.user_id == user_id).count()
    mails = db.query(EmailLog).filter(EmailLog.recipient_user_id == user_id).count()
    return bell, mails


def _with_default(channel: NotificationChannel) -> set[str]:
    return {r for r, spec in admin_alerts.OPS_REASONS.items() if spec.default == channel}


def test_the_registry_sorts_every_reason_into_an_alarm_or_a_notice():
    assert _with_default(NotificationChannel.both) == ALARMS
    assert _with_default(NotificationChannel.in_app) == NOTICES
    assert set(admin_alerts.OPS_REASONS) == ALARMS | NOTICES
    assert {s.group for s in admin_alerts.OPS_REASONS.values()} == {
        "antivirus", "storage", "tasks", "services", "security", "updates", "housekeeping",
    }


def test_an_alarm_is_mailed_by_default_and_a_notice_only_rings_the_bell(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin)

    _send(db, "av_scan_failed")
    assert _got(db, admin.id) == (1, 1)

    _send(db, "update_completed")
    assert _got(db, admin.id) == (2, 1)


def test_each_alert_type_is_switched_on_its_own(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    notification_prefs.update_preferences(
        db, admin, {"ops_alert:av_scan_failed": "in_app", "ops_alert:update_completed": "both"}
    )
    db.commit()

    _send(db, "av_scan_failed")  # an alarm, turned down to the bell
    assert _got(db, admin.id) == (1, 0)
    _send(db, "update_completed")  # a notice, turned up to a mail
    assert _got(db, admin.id) == (2, 1)
    _send(db, "storage_critical_low")  # untouched: still mailed
    assert _got(db, admin.id) == (3, 2)

    notification_prefs.update_preferences(db, admin, {"ops_alert:storage_critical_low": "off"})
    db.commit()
    _send(db, "storage_critical_low")
    assert _got(db, admin.id) == (3, 2)


def test_a_choice_made_for_all_operational_alerts_stays_the_fallback(db, make_user):
    """An admin who set "Operational alerts" before it was split keeps that
    choice for every type, until they change one."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    db.add(
        UserNotificationPreference(
            user_id=admin.id,
            category=NotificationCategory.ops_alert,
            channel=NotificationChannel.both,
        )
    )
    db.commit()

    _send(db, "update_completed")  # a notice by default; their choice says mail
    assert _got(db, admin.id) == (1, 1)

    rows = [r for r in notification_prefs.list_preferences(db, admin) if r.topic]
    assert {r.channel for r in rows} == {NotificationChannel.both}

    notification_prefs.update_preferences(db, admin, {"ops_alert:update_completed": "in_app"})
    db.commit()
    _send(db, "update_completed")
    assert _got(db, admin.id) == (2, 1)
    _send(db, "update_triggered")  # the others still follow the old choice
    assert _got(db, admin.id) == (3, 2)


def test_the_list_has_one_row_per_alert_type_for_an_admin_only(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    client = make_user(email="client@test.local", role=UserRole.client)

    rows = notification_prefs.list_preferences(db, admin)
    ops = [r for r in rows if r.category == NotificationCategory.ops_alert]
    assert [r.topic for r in ops] == list(admin_alerts.OPS_REASONS)
    assert all(r.group == admin_alerts.OPS_REASONS[r.topic].group for r in ops)
    assert all(r.channel == admin_alerts.OPS_REASONS[r.topic].default for r in ops)
    # Changeable here, never by one tap from a mail.
    assert all(r.locked is False and r.one_click is False for r in ops)
    # Every other category keeps its single row.
    others = [r for r in rows if r.category != NotificationCategory.ops_alert]
    assert all(r.topic is None for r in others)
    assert len({r.category for r in others}) == len(others)
    # The grouped rows close the list: a plain row after the last group's
    # heading would read as part of that group.
    assert rows[-len(ops):] == ops

    assert not [
        r for r in notification_prefs.list_preferences(db, client)
        if r.category == NotificationCategory.ops_alert
    ]


def test_an_unknown_alert_type_and_a_non_admin_are_refused(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    client = make_user(email="client@test.local", role=UserRole.client)

    for user, key in ((admin, "ops_alert:no_such_alarm"), (admin, "share_created:anything"),
                      (client, "ops_alert:av_scan_failed")):
        with pytest.raises(AppError) as refused:
            notification_prefs.update_preferences(db, user, {key: "off"})
        assert refused.value.code == "INVALID_CATEGORY"
    assert db.query(UserNotificationTopicPreference).count() == 0


def test_an_alarm_nobody_registered_still_reaches_the_inbox(db, make_user, monkeypatch):
    """Production does not raise on an unregistered reason (conftest makes tests
    do so): it is delivered, on the "something is broken" default."""
    monkeypatch.setattr(admin_alerts, "STRICT_REASONS", False)
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    _send(db, "brand_new_alarm")
    assert _got(db, admin.id) == (1, 1)


@pytest.mark.asyncio
async def test_the_preferences_route_takes_and_returns_alert_types(make_user, client, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/notifications/preferences",
        headers=auth,
        json={"preferences": {"ops_alert:av_scan_failed": "off", "ops_alert:update_failed": "email"}},
    )
    assert resp.status_code == 200, resp.text
    ops = {i["topic"]: i for i in resp.json()["items"] if i["category"] == "ops_alert"}
    assert set(ops) == set(admin_alerts.OPS_REASONS)
    assert ops["av_scan_failed"]["channel"] == "off"
    assert ops["update_failed"]["channel"] == "email"
    assert ops["av_unhealthy"]["channel"] == "both"
    assert ops["av_scan_failed"]["group"] == "antivirus"
    assert ops["av_scan_failed"]["one_click"] is False

    bad = await client.put(
        "/api/notifications/preferences", headers=auth,
        json={"preferences": {"ops_alert:no_such_alarm": "off"}},
    )
    assert bad.status_code == 400 and bad.json()["code"] == "INVALID_CATEGORY"
