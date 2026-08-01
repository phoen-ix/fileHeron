"""Per-share `notify_recipients` flag + admin kv default + group fan-out.

The announcement is DEFERRED until the share's uploads land - a share is empty
at create time, because files attach at upload (audit #2). These tests used to
observe the fan-out on an empty share, which is exactly the shape that let
"shared 0 files with you" ship: they asserted THAT a notification was sent and
never looked at what it said. `land_file_and_announce` is the second half of the
real flow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.share import ShareKind
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from app.services import group as group_svc
from app.services import settings as settings_svc
from app.services import share as share_svc

from ._share_helpers import land_file_and_announce


def _future() -> datetime:
    return (datetime.now(tz=timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)


def _patch_email_capture(monkeypatch):
    """Capture every send_email_job enqueue. Tests can read this list to
    assert which addresses were emailed (or that none were)."""
    enqueued: list[dict] = []

    def _capture(*args, **kwargs):
        if args and args[0] == "send_email_job":
            enqueued.append(kwargs)

    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue", _capture
    )
    return enqueued


def test_default_kv_true_dispatches_to_direct_recipient(
    make_user, db, monkeypatch
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    rows = (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_created,
        )
        .all()
    )
    assert len(rows) == 1
    assert any(kw.get("to") == rec.email for kw in enqueued)


def test_explicit_false_skips_dispatch(make_user, db, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
        notify_recipients=False,
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    rows = (
        db.query(Notification)
        .filter(Notification.user_id == rec.id)
        .all()
    )
    assert rows == []
    assert enqueued == []


def test_kv_false_no_field_skips_dispatch(make_user, db, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT,
        value="false",
        actor=admin,
    )
    db.commit()
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    assert (
        db.query(Notification).filter(Notification.user_id == rec.id).count() == 0
    )
    assert enqueued == []


def test_explicit_true_overrides_kv_false(make_user, db, monkeypatch):
    """Sender's explicit True wins over a kv default of False."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT,
        value="false",
        actor=admin,
    )
    db.commit()
    _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
        notify_recipients=True,
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    assert (
        db.query(Notification).filter(Notification.user_id == rec.id).count() == 1
    )


def test_group_fan_out_to_all_active_members(make_user, db, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="a@test.local", role=UserRole.client)
    bob = make_user(email="b@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="team", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=alice)
    group_svc.add_member(db, actor=admin, group=g, user=bob)
    db.commit()
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    notified_user_ids = {
        n.user_id
        for n in db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_created)
        .all()
    }
    assert notified_user_ids == {alice.id, bob.id}
    addresses = {kw.get("to") for kw in enqueued}
    assert addresses == {alice.email, bob.email}


def test_group_fan_out_excludes_sender(make_user, db, monkeypatch):
    """When the sender is also a member of the target group they shouldn't
    receive a self-notification."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="a@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="team", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=admin)
    group_svc.add_member(db, actor=admin, group=g, user=alice)
    db.commit()
    _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    notified_user_ids = {
        n.user_id
        for n in db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_created)
        .all()
    }
    assert notified_user_ids == {alice.id}


def test_group_fan_out_excludes_disabled_member(
    make_user, db, monkeypatch
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    active = make_user(email="active@test.local", role=UserRole.client)
    later_disabled = make_user(
        email="dis@test.local", role=UserRole.client
    )
    g = group_svc.create_group(
        db, actor=admin, name="team", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=active)
    # Add while active, then disable - mirrors the realistic lifecycle
    # (admin disables a user who was already in groups). The fan-out
    # filter must skip them at SQL level.
    group_svc.add_member(db, actor=admin, group=g, user=later_disabled)
    later_disabled.is_disabled = True
    db.commit()
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    notified_user_ids = {
        n.user_id
        for n in db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_created)
        .all()
    }
    assert notified_user_ids == {active.id}
    assert later_disabled.email not in {kw.get("to") for kw in enqueued}


def test_direct_and_group_overlap_dedupes_to_single_dispatch(
    make_user, db, monkeypatch
):
    """User who is BOTH a direct recipient AND a group member should
    receive exactly one notification."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    overlap = make_user(email="o@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="team", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=overlap)
    db.commit()
    _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[overlap.id],
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    rows = (
        db.query(Notification)
        .filter(
            Notification.user_id == overlap.id,
            Notification.category == NotificationCategory.share_created,
        )
        .all()
    )
    assert len(rows) == 1


def test_recipient_with_off_preference_still_blocked_when_notify_true(
    make_user, db, monkeypatch
):
    """Per-user channel `off` is honoured by the dispatch funnel - confirms
    the new gate didn't bypass the funnel."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    db.add(
        UserNotificationPreference(
            user_id=rec.id,
            category=NotificationCategory.share_created,
            channel=NotificationChannel.off,
        )
    )
    db.commit()
    enqueued = _patch_email_capture(monkeypatch)

    sh = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
        notify_recipients=True,
    )
    land_file_and_announce(db, sh, admin)
    db.commit()

    assert (
        db.query(Notification).filter(Notification.user_id == rec.id).count() == 0
    )
    assert enqueued == []


@pytest.mark.asyncio
async def test_http_endpoint_passes_field_through(
    make_user, db, client, login_as, monkeypatch
):
    """End-to-end via HTTP: payload field must reach the service."""
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="rec@test.local", role=UserRole.client)
    enqueued = _patch_email_capture(monkeypatch)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    expires_iso = (
        datetime.now(tz=timezone.utc) + timedelta(days=1)
    ).isoformat()
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": expires_iso,
            "subject": "no-mail",
            "message": None,
            "notify_recipients": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert (
        db.query(Notification).filter(Notification.user_id == rec.id).count() == 0
    )
    # First login can fire a `login_alert` to the sender; filter to
    # mails actually destined for the recipient - that's what this gate
    # is responsible for.
    assert all(kw.get("to") != rec.email for kw in enqueued)
