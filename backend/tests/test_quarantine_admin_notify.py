"""Quarantine notification fan-out - behavior of the
`quarantine.notify_admins` setting and its admin-facing endpoint."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.notification import Notification, NotificationCategory
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import quarantine as q_svc
from app.services import settings as settings_svc


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_and_quarantine(db, sender, tmp_path, monkeypatch, file_id="qn-uuid-1"):
    """Seed a share + file and quarantine it. Returns the file."""
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    on_disk = tmp_path / f"{file_id}.bin"
    on_disk.write_bytes(b"x")
    f = File(
        id=file_id,
        share_id=share.id,
        original_filename="upload.bin",
        mime_type="application/octet-stream",
        size_bytes=1,
        state=FileState.ready_unscanned,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
    )
    db.add(f)
    db.commit()

    monkeypatch.setattr("app.config.settings.QUARANTINE_DIR", str(tmp_path / "quarantine"))
    q_svc.quarantine_file(db, file=f, signature="Eicar-Test-Signature")
    db.commit()
    return f


def _quarantined_rows_for(db, user_id: int) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.category == NotificationCategory.file_quarantined,
        )
        .all()
    )


def test_notify_admins_off_only_uploader_gets_row(
    make_user, db, tmp_path, monkeypatch
):
    """Default (toggle off) - only the uploader sees a notification row."""
    sender = make_user(email="up@test.local", role=UserRole.employee)
    admin1 = make_user(email="ad1@test.local", role=UserRole.admin)
    admin2 = make_user(email="ad2@test.local", role=UserRole.admin)

    _seed_and_quarantine(db, sender, tmp_path, monkeypatch)

    assert len(_quarantined_rows_for(db, sender.id)) == 1
    assert _quarantined_rows_for(db, admin1.id) == []
    assert _quarantined_rows_for(db, admin2.id) == []


def test_notify_admins_on_each_admin_gets_row(
    make_user, db, tmp_path, monkeypatch
):
    sender = make_user(email="up@test.local", role=UserRole.employee)
    admin1 = make_user(email="ad1@test.local", role=UserRole.admin)
    admin2 = make_user(email="ad2@test.local", role=UserRole.admin)

    settings_svc.set_value(
        db, key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, value="true", actor=None
    )
    db.commit()

    enqueued = []
    monkeypatch.setattr(
        "app.services.notification.job_queue.enqueue",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )

    _seed_and_quarantine(db, sender, tmp_path, monkeypatch)

    assert len(_quarantined_rows_for(db, sender.id)) == 1
    assert len(_quarantined_rows_for(db, admin1.id)) == 1
    assert len(_quarantined_rows_for(db, admin2.id)) == 1

    # Plaintext email is stored on the user row now, so the dispatcher
    # also enqueues a send_email_job for the uploader + each admin
    # (default channel for file_quarantined is "both").
    enqueued_to = sorted(kwargs.get("to") for _args, kwargs in enqueued)
    assert enqueued_to == sorted(["up@test.local", "ad1@test.local", "ad2@test.local"])


def test_disabled_admin_skipped(
    make_user, db, tmp_path, monkeypatch
):
    sender = make_user(email="up@test.local", role=UserRole.employee)
    on_admin = make_user(email="ad-on@test.local", role=UserRole.admin)
    off_admin = make_user(
        email="ad-off@test.local", role=UserRole.admin, is_disabled=True
    )

    settings_svc.set_value(
        db, key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, value="true", actor=None
    )
    db.commit()

    _seed_and_quarantine(db, sender, tmp_path, monkeypatch)

    assert len(_quarantined_rows_for(db, on_admin.id)) == 1
    assert _quarantined_rows_for(db, off_admin.id) == []


def test_notify_admins_skips_admin_uploader_to_avoid_double_notify(
    make_user, db, tmp_path, monkeypatch
):
    """If the uploader IS an admin, they should get exactly one row,
    not two (one as uploader + one as admin recipient)."""
    admin_uploader = make_user(email="ad-up@test.local", role=UserRole.admin)
    other_admin = make_user(email="ad-other@test.local", role=UserRole.admin)

    settings_svc.set_value(
        db, key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, value="true", actor=None
    )
    db.commit()

    _seed_and_quarantine(db, admin_uploader, tmp_path, monkeypatch)

    assert len(_quarantined_rows_for(db, admin_uploader.id)) == 1
    assert len(_quarantined_rows_for(db, other_admin.id)) == 1


@pytest.mark.asyncio
async def test_settings_put_writes_audit_row(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.put(
        "/api/admin/settings/quarantine",
        json={"notify_admins": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"notify_admins": True}

    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.quarantine_policy_changed.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == admin.id
    assert audits[0].extra == {"notify_admins": True}

    resp_get = await client.get(
        "/api/admin/settings/quarantine",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_get.status_code == 200
    assert resp_get.json() == {"notify_admins": True}


# ---------------------------------------------------------------------------
# How it ended: the uploader is told
# ---------------------------------------------------------------------------
#
# The uploader was told "upload.bin was quarantined by antivirus" and then
# nothing: not when an admin released it as a false positive, not when it was
# purged. Only the audit log knew (decided with the user, 2026-11-21).


def _rows(db, user_id: int, category: NotificationCategory) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.category == category)
        .all()
    )


def test_a_release_tells_the_uploader_and_links_the_share(make_user, db, tmp_path, monkeypatch):
    from app.services import quarantine_admin as qadmin_svc

    sender = make_user(email="up@test.local", role=UserRole.employee)
    admin = make_user(email="ad@test.local", role=UserRole.admin)
    monkeypatch.setattr("app.config.settings.STORAGE_ROOT", str(tmp_path / "out"))
    f = _seed_and_quarantine(db, sender, tmp_path, monkeypatch)

    qadmin_svc.release(db, admin=admin, file=f, reason="manual review: a false positive")
    db.commit()

    told = _rows(db, sender.id, NotificationCategory.file_quarantine_released)
    assert len(told) == 1
    assert told[0].payload_json["filename"] == "upload.bin"
    assert told[0].link_url.endswith(f"/share/{f.share_id}")
    # The admin's reason is a note for the audit log, not for the uploader.
    assert "false positive" not in str(told[0].payload_json)
    assert _rows(db, sender.id, NotificationCategory.file_quarantine_purged) == []
    assert _rows(db, admin.id, NotificationCategory.file_quarantine_released) == []


def test_a_purge_tells_the_uploader(make_user, db, tmp_path, monkeypatch):
    from app.services import quarantine_admin as qadmin_svc

    sender = make_user(email="up@test.local", role=UserRole.employee)
    admin = make_user(email="ad@test.local", role=UserRole.admin)
    f = _seed_and_quarantine(db, sender, tmp_path, monkeypatch)

    qadmin_svc.purge(db, admin=admin, file=f)
    db.commit()

    told = _rows(db, sender.id, NotificationCategory.file_quarantine_purged)
    assert len(told) == 1
    assert told[0].payload_json["filename"] == "upload.bin"
    assert _rows(db, sender.id, NotificationCategory.file_quarantine_released) == []


def test_an_admin_is_not_told_about_what_they_did_to_their_own_upload(
    make_user, db, tmp_path, monkeypatch
):
    from app.services import quarantine_admin as qadmin_svc

    admin = make_user(email="ad@test.local", role=UserRole.admin)
    f = _seed_and_quarantine(db, admin, tmp_path, monkeypatch)

    qadmin_svc.purge(db, admin=admin, file=f)
    db.commit()

    assert _rows(db, admin.id, NotificationCategory.file_quarantine_purged) == []


def test_the_retention_task_tells_the_uploader_too(make_user, db, tmp_path, monkeypatch):
    """Two code paths purge a quarantined file - the admin's button and the
    nightly retention task. The notice follows the outcome, not the path."""
    import asyncio

    from app.workers import purge_old_quarantine as pq

    sender = make_user(email="up@test.local", role=UserRole.employee)
    f = _seed_and_quarantine(db, sender, tmp_path, monkeypatch)
    # Quarantined well past the retention window.
    db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.file_quarantined.value, AuditLog.target_id == f.id
    ).update({"created_at": _now_naive() - timedelta(days=365)})
    db.commit()

    asyncio.run(pq.purge_old_quarantine(None))

    db.expire_all()
    assert db.get(File, f.id).storage_path is None, "the task did not purge - the test proves nothing"
    assert len(_rows(db, sender.id, NotificationCategory.file_quarantine_purged)) == 1
