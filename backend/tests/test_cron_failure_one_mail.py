"""One failed job, one mail per admin (v2.25.2).

A cron failure goes out twice: as an ops alert (cron_tracker, mailed to admins
who opted ops alerts into mail) and through the error-alert pipeline
(notify_admin_error, mailed by default). fh@ got both for the same IMAP blip on
2026-09-18 and 2026-10-07. Where the error-alert pipeline will mail an admin,
the ops alert now stays in the bell; everywhere else nothing changes.
"""
from __future__ import annotations

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from app.services import cron_tracker
from app.services import settings as s


@pytest.fixture
def admin(make_user, db):
    a = make_user(email="ops@test.local", role=UserRole.admin)
    # This admin asked for ops alerts by mail - the case that got two mails.
    db.add(
        UserNotificationPreference(
            user_id=a.id, category=NotificationCategory.ops_alert, channel=NotificationChannel.both
        )
    )
    db.commit()
    return a


@pytest.fixture
def mails(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda name, **kw: sent.append(kw) if name == "send_email_job" else None,
    )
    return sent


def _fail(db, job="imap_poll"):
    cron_tracker._maybe_alert_admins(db, job, "OSError: [Errno 101] Network is unreachable")
    db.commit()


def _alerts_on(db, mode="admins"):
    s.set_value(db, key=s.Keys.ERROR_ALERT_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.ERROR_ALERT_RECIPIENTS_MODE, value=mode, actor=None)
    db.commit()


def _bell(db, admin):
    return (
        db.query(Notification)
        .filter(Notification.user_id == admin.id, Notification.category == NotificationCategory.ops_alert)
        .count()
    )


def test_the_ops_alert_stays_in_the_bell_when_the_error_alert_mails_the_admin(db, admin, mails):
    _alerts_on(db)
    _fail(db)
    assert mails == []  # the error-alert pipeline sends this admin's one mail
    assert _bell(db, admin) == 1


def test_with_error_alerts_off_the_ops_alert_still_mails(db, admin, mails):
    _fail(db)
    assert [m["to"] for m in mails] == [admin.email]


def test_custom_error_recipients_do_not_silence_the_admins_mail(db, admin, mails):
    _alerts_on(db, mode="custom")
    _fail(db)
    assert [m["to"] for m in mails] == [admin.email]


def test_an_admin_who_turned_error_mail_off_still_gets_the_ops_mail(db, admin, mails):
    _alerts_on(db)
    db.add(
        UserNotificationPreference(
            user_id=admin.id,
            category=NotificationCategory.server_error,
            channel=NotificationChannel.in_app,
        )
    )
    db.commit()
    _fail(db)
    assert [m["to"] for m in mails] == [admin.email]


def test_a_job_whose_error_alert_is_switched_off_still_mails(db, admin, mails):
    _alerts_on(db)
    s.set_value(db, key="cron.imap_poll.alert_on_failure", value="false", actor=None)
    db.commit()
    _fail(db)
    assert [m["to"] for m in mails] == [admin.email]
