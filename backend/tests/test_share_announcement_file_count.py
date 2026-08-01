"""The share announcement must describe the share.

Audit #2. Files attach at UPLOAD time (`file_svc.create_pending` sets
`files.share_id`), and every client creates the share first and uploads into it
afterwards - the SPA posts the form and then starts Uppy, the desktop client
does the same. `create_share` dispatched `share_created` at the end of that
first step, so `file_count` was computed over an empty share.

Every outbound share this product has ever sent told its recipient "Alice
shared 0 files with you" (DE: "hat 0 Dateien mit dir geteilt"), in the email, in
the bell entry, and linked them to a share page reading "Files (0)" until the
uploads finished. For an inbound share the same 0-file message fanned out to
every employee and admin.

It survived because every test that asserted on the payload hand-built one, and
every test that drove the real `create_share` asserted only WHICH users were
notified - never what they were told.
"""
from __future__ import annotations

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.share import ShareKind, ShareState
from app.models.user import UserRole
from app.services import share as share_svc

from ._share_helpers import land_file, land_file_and_announce


def _future():
    from datetime import timedelta

    from app.utils.timeutil import utc_now

    return utc_now() + timedelta(hours=1)


def _announcements(db, user_id: int) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.category == NotificationCategory.share_created,
        )
        .all()
    )


def test_the_announcement_counts_the_files_that_were_actually_shared(db, make_user):
    sender = make_user(email="s@test.local", role=UserRole.employee)
    rec = make_user(email="r@test.local", role=UserRole.employee)

    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file(db, share, sender, name="a.pdf")
    land_file(db, share, sender, name="b.pdf")
    land_file(db, share, sender, name="c.pdf")
    share_svc.announce_if_ready(db, share.id)
    db.commit()

    rows = _announcements(db, rec.id)
    assert len(rows) == 1, "exactly one announcement per share"
    assert rows[0].payload_json["file_count"] == 3, (
        "the recipient was told how many files a share had before any of them "
        "had been uploaded"
    )


def test_nothing_is_sent_while_the_share_is_still_empty(db, make_user):
    """The moment the old code announced. A recipient must not hear about a
    share that has nothing in it yet - they would open an empty page."""
    sender = make_user(email="s2@test.local", role=UserRole.employee)
    rec = make_user(email="r2@test.local", role=UserRole.employee)

    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    db.commit()
    assert _announcements(db, rec.id) == []


def test_an_upload_still_in_flight_holds_the_announcement(db, make_user):
    """Announcing on the FIRST file to land would restore a wrong count, just a
    smaller one. The share announces when nothing is left uploading."""
    from app.models.file import File, FileState

    sender = make_user(email="s3@test.local", role=UserRole.employee)
    rec = make_user(email="r3@test.local", role=UserRole.employee)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file(db, share, sender, name="done.pdf")
    db.add(
        File(
            id="00000000-0000-0000-0000-0000000ann01",
            share_id=share.id,
            original_filename="still-going.pdf",
            mime_type="application/pdf",
            size_bytes=99,
            state=FileState.uploading,
            uploaded_by_id=sender.id,
        )
    )
    db.flush()

    assert share_svc.announce_if_ready(db, share.id) is False
    db.commit()
    assert _announcements(db, rec.id) == []


def test_the_announcement_is_sent_exactly_once(db, make_user):
    """Every finalize calls it, so the second, third and hundredth file must
    find nothing left to do."""
    sender = make_user(email="s4@test.local", role=UserRole.employee)
    rec = make_user(email="r4@test.local", role=UserRole.employee)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file(db, share, sender)
    assert share_svc.announce_if_ready(db, share.id) is True
    assert share_svc.announce_if_ready(db, share.id) is False
    assert share_svc.announce_if_ready(db, share.id) is False
    db.commit()
    assert len(_announcements(db, rec.id)) == 1


def test_the_batch_complete_signal_does_not_double_announce(db, make_user):
    """The SPA posts `files-added` after the initial batch. That must BE the
    announcement, not a `share_files_added` follow-up on top of one."""
    sender = make_user(email="s5@test.local", role=UserRole.employee)
    rec = make_user(email="r5@test.local", role=UserRole.employee)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    f = land_file(db, share, sender)
    share_svc.register_files_added(
        db, user=sender, share=share, file_ids=[f.id], notify=True
    )
    db.commit()

    assert len(_announcements(db, rec.id)) == 1
    added = (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_files_added,
        )
        .count()
    )
    assert added == 0, "the recipient was told about a share and then about an addition to it"


def test_a_later_batch_is_still_a_files_added_notification(db, make_user):
    """The control: once the share HAS announced, adding to it is an addition."""
    sender = make_user(email="s6@test.local", role=UserRole.employee)
    rec = make_user(email="r6@test.local", role=UserRole.employee)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    land_file_and_announce(db, share, sender, name="first.pdf")
    db.commit()

    second = land_file(db, share, sender, name="second.pdf")
    share_svc.register_files_added(
        db, user=sender, share=share, file_ids=[second.id], notify=True
    )
    db.commit()

    assert len(_announcements(db, rec.id)) == 1
    added = (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_files_added,
        )
        .count()
    )
    assert added == 1


def test_an_approved_share_announces_with_its_real_count(db, make_user, monkeypatch):
    """The approval path already deferred to activation; it must keep counting
    what is there at that moment."""
    import json

    from app.services import settings as settings_svc

    k = settings_svc.Keys
    admin = make_user(email="ap-admin@test.local", role=UserRole.admin)
    sender = make_user(email="s7@test.local", role=UserRole.employee)
    rec = make_user(email="r7@test.local", role=UserRole.employee)
    for key, value in (
        (k.SHARE_APPROVAL_ENABLED, "true"),
        (k.SHARE_APPROVAL_APPROVER_MODE, "admins_only"),
        (k.SHARE_APPROVAL_SCOPE, "outbound"),
        (k.SHARE_APPROVAL_APPROVER_USERS, json.dumps([admin.id])),
    ):
        settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()

    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    assert share.state == ShareState.pending_approval
    land_file(db, share, sender, name="x.pdf")
    land_file(db, share, sender, name="y.pdf")
    db.commit()

    share_svc.approve_share(db, user=admin, share=share)
    db.commit()

    rows = _announcements(db, rec.id)
    assert len(rows) == 1
    assert rows[0].payload_json["file_count"] == 2


@pytest.mark.parametrize("kind", [ShareKind.inbound])
def test_inbound_staff_fan_out_also_waits_for_the_files(db, make_user, kind):
    """Inbound ignores the notify flag and tells every staff member, so the
    0-file message reached the widest audience of all."""
    client = make_user(email="c@test.local", role=UserRole.employee)
    emp = make_user(email="e@test.local", role=UserRole.employee)

    share = share_svc.create_share(
        db, created_by=client, kind=kind, expires_at=None
    )
    db.commit()
    assert _announcements(db, emp.id) == []

    land_file_and_announce(db, share, client, name="invoice.pdf")
    db.commit()
    rows = _announcements(db, emp.id)
    assert len(rows) == 1
    assert rows[0].payload_json["file_count"] == 1
