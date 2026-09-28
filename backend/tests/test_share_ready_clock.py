"""A share's clock and its recipient mail start when its files can be downloaded
(v2.23.0).

Three defects, one moment:

- `expire_files` expired a share while its upload was still running: the
  `uploading` row went `deleted`, tusd kept accepting bytes, and a 20 GB transfer
  was refused at pre-finish, after its last byte.
- A preset expiry ("1 hour") counted from the click, so the upload ate into it.
- The recipient was mailed as soon as a file LANDED - `ready_unscanned`, whose
  download answers 425 SCAN_IN_PROGRESS - and "files added" went out the moment
  the owner's client reported the upload done, before the scan.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.notification import Notification, NotificationCategory
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import share as share_svc
from app.services import share_approval as approval_svc
from app.utils.timeutil import utc_now

from ._share_helpers import land_file

PW = "Pass12345678!"


def _people(make_user):
    sender = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    return sender, rec


def _notes(db, user_id, category):
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.category == category)
        .all()
    )


def _share(db, sender, rec, **kw) -> Share:
    kw.setdefault("expires_at", utc_now() + timedelta(days=1))
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        **kw,
    )
    db.commit()
    return share


def _uploading(db, share, uploader, *, last_activity) -> File:
    f = land_file(db, share, uploader, state=FileState.uploading)
    f.storage_path = None
    f.created_at = last_activity
    f.last_progress_at = last_activity
    db.commit()
    return f


# ---- 1. never expire during a live upload --------------------------------


@pytest.mark.asyncio
async def test_a_live_upload_holds_the_share_past_its_expiry(db, make_user):
    from app.workers.expire_files import expire_files

    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    f = _uploading(db, share, sender, last_activity=utc_now())
    share.expires_at = utc_now() - timedelta(minutes=5)
    db.commit()

    await expire_files(None)
    db.expire_all()

    assert db.get(Share, share.id).state == ShareState.active
    assert db.get(File, f.id).state == FileState.uploading, "the transfer was not killed"


@pytest.mark.asyncio
async def test_a_stalled_upload_does_not_hold_it_forever(db, make_user):
    from app.workers.expire_files import expire_files

    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    _uploading(db, share, sender, last_activity=utc_now() - timedelta(hours=30))
    share.expires_at = utc_now() - timedelta(minutes=5)
    db.commit()

    await expire_files(None)
    db.expire_all()

    assert db.get(Share, share.id).state == ShareState.expired


@pytest.mark.asyncio
async def test_without_an_upload_expiry_is_unchanged(db, make_user):
    from app.workers.expire_files import expire_files

    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    land_file(db, share, sender)
    share.expires_at = utc_now() - timedelta(minutes=5)
    db.commit()

    await expire_files(None)
    db.expire_all()

    assert db.get(Share, share.id).state == ShareState.expired


# ---- 2. the recipient is mailed only when the download works -------------


def test_a_file_still_being_scanned_is_not_announced(db, make_user):
    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    f = land_file(db, share, sender, state=FileState.ready_unscanned)
    db.commit()

    assert share_svc.announce_if_ready(db, share.id) is False
    db.commit()
    assert _notes(db, rec.id, NotificationCategory.share_created) == []

    f.state = FileState.clean
    db.commit()
    assert share_svc.announce_if_ready(db, share.id) is True
    db.commit()
    assert len(_notes(db, rec.id, NotificationCategory.share_created)) == 1


def test_without_the_batch_signal_the_trigger_waits_for_quiet(db, make_user):
    """A client that never reports its batch complete gets the old guess: a
    freshly created file might be one of several, so the trigger waits out the
    quiet window and the sweep announces after it."""
    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    land_file(db, share, sender)
    db.commit()

    assert share_svc.notify_if_downloadable(db, share.id) is False
    assert db.get(Share, share.id).upload_batch_done is False


@pytest.mark.asyncio
async def test_the_scan_finishing_sends_the_mail(db, make_user, tmp_path, monkeypatch):
    """The real trigger: the worker that flips the file to clean announces the
    share, once the owner's client has said its batch is done."""
    from app.services import av_scan as av_scan_svc
    from app.services.av_scan import ScanResult
    from app.workers import av_scan as worker_mod

    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    on_disk = tmp_path / "file.bin"
    on_disk.write_bytes(b"hello")
    f = land_file(db, share, sender, size=5, state=FileState.ready_unscanned)
    f.storage_path = str(on_disk)
    db.commit()
    share_svc.register_files_added(db, user=sender, share=share, file_ids=[f.id], notify=True)
    db.commit()
    assert _notes(db, rec.id, NotificationCategory.share_created) == [], "not before the scan"

    monkeypatch.setattr(av_scan_svc, "scan_path", lambda _p: ScanResult(state="clean", signature=None, raw="OK"))
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    await worker_mod.av_scan_file(None, f.id)

    assert len(_notes(db, rec.id, NotificationCategory.share_created)) == 1


def test_files_added_later_wait_for_their_scan(db, make_user):
    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    land_file(db, share, sender)
    assert share_svc.announce_if_ready(db, share.id)
    db.commit()

    new = land_file(db, share, sender, name="more.bin", state=FileState.ready_unscanned)
    db.commit()
    share_svc.register_files_added(db, user=sender, share=share, file_ids=[new.id], notify=True)
    db.commit()
    assert _notes(db, rec.id, NotificationCategory.share_files_added) == []
    assert db.get(Share, share.id).pending_added_notice == 1

    new.state = FileState.clean
    db.commit()
    assert share_svc.notify_if_downloadable(db, share.id) is True
    db.commit()
    (note,) = _notes(db, rec.id, NotificationCategory.share_files_added)
    assert note.payload_json["added_count"] == 1
    assert db.get(Share, share.id).pending_added_notice is None

    assert share_svc.notify_if_downloadable(db, share.id) is False, "sent once"


def test_files_added_before_the_announcement_are_not_announced_twice(db, make_user):
    """The owner's batch signal arrives while the first files are being scanned:
    the share's own announcement is still owed and will count them, so no
    separate "files added" notice may go out."""
    sender, rec = _people(make_user)
    share = _share(db, sender, rec)
    f = land_file(db, share, sender, state=FileState.ready_unscanned)
    db.commit()
    share_svc.register_files_added(db, user=sender, share=share, file_ids=[f.id], notify=True)
    db.commit()

    assert _notes(db, rec.id, NotificationCategory.share_files_added) == []
    assert db.get(Share, share.id).pending_added_notice is None


# ---- 3. a preset counts from ready ----------------------------------------


def test_a_preset_starts_its_clock_when_the_files_are_ready(db, make_user):
    sender, rec = _people(make_user)
    share = _share(db, sender, rec, expires_at=None, expires_in_sec=3600)
    assert share.expires_at is None and share.expires_in_sec == 3600

    land_file(db, share, sender)
    before = utc_now()
    assert share_svc.announce_if_ready(db, share.id)
    db.commit()
    db.refresh(share)

    assert share.expires_in_sec is None
    assert before + timedelta(minutes=59) < share.expires_at <= utc_now() + timedelta(hours=1)
    (note,) = _notes(db, rec.id, NotificationCategory.share_created)
    assert note.payload_json["expires_at"] is not None, "the mail states the real expiry"


def test_changing_the_expiry_replaces_a_pending_preset(db, make_user):
    sender, rec = _people(make_user)
    share = _share(db, sender, rec, expires_at=None, expires_in_sec=3600)
    exact = utc_now() + timedelta(days=3)

    share_svc.update_share_expiry(db, user=sender, share=share, new_expires_at=exact)
    db.commit()
    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()
    db.refresh(share)

    assert share.expires_in_sec is None
    assert abs((share.expires_at - exact).total_seconds()) < 2, "the owner's time stands"


def test_a_held_share_with_a_preset_can_still_be_approved(db, make_user):
    """No clock, nothing to have passed: the approval must not refuse it as
    expired, and the clock starts once it is approved and ready."""
    for key, value in (
        (settings_svc.Keys.SHARE_APPROVAL_ENABLED, "true"),
        (settings_svc.Keys.SHARE_APPROVAL_APPROVER_MODE, "admins_only"),
        (settings_svc.Keys.SHARE_APPROVAL_SCOPE, "outbound"),
    ):
        settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    sender, rec = _people(make_user)
    share = _share(db, sender, rec, expires_at=None, expires_in_sec=60)
    assert share.state == ShareState.pending_approval
    land_file(db, share, sender)
    db.commit()

    share_svc.approve_share(
        db, user=admin, share=share,
        expect_fingerprint=approval_svc.content_fingerprint(db, share),
    )
    db.commit()
    db.refresh(share)

    assert share.state == ShareState.active
    assert share.expires_at is not None and share.expires_in_sec is None


def _future_iso() -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()


@pytest.mark.asyncio
async def test_the_api_takes_a_preset_or_an_exact_time_not_both(make_user, db, client, login_as):
    sender, rec = _people(make_user)
    token, _ = await login_as("emp@test.local", PW)
    hdr = {"Authorization": f"Bearer {token}"}
    body = {
        "kind": "outbound",
        "recipients": {"user_ids": [rec.id], "group_ids": []},
        "expires_at": None,
        "expires_in_sec": 3600,
    }

    ok = await client.post("/api/shares", json=body, headers=hdr)
    assert ok.status_code == 201, ok.text
    assert ok.json()["expires_at"] is None and ok.json()["expires_in_sec"] == 3600

    both = await client.post(
        "/api/shares", json={**body, "expires_at": _future_iso()}, headers=hdr
    )
    assert both.status_code == 422, both.text

    listed = await client.get("/api/shares", params={"box": "outbox"}, headers=hdr)
    assert listed.json()["items"][0]["expires_in_sec"] == 3600
