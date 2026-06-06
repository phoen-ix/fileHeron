"""POST /api/shares/{id}/files-added - owner's batch-complete signal after
adding files to an active share: audit + opt-in recipient notification."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.notification import Notification, NotificationCategory
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole


def _make_share(db, owner, *, state=ShareState.active, kind=ShareKind.outbound):
    share = Share(created_by_id=owner.id, kind=kind, state=state, subject="Docs")
    db.add(share)
    db.flush()
    return share


def _add_file(db, share, owner, name="a.bin"):
    f = File(
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=10,
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    return f


@pytest.mark.asyncio
async def test_files_added_owner_audits_and_notifies(db, make_user, client, login_as):
    owner = make_user(email="owner@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="rcpt@test.local", role=UserRole.client)
    share = _make_share(db, owner)
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    f1 = _add_file(db, share, owner, "a.bin")
    f2 = _add_file(db, share, owner, "b.bin")
    db.commit()

    token, _ = await login_as("owner@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/shares/{share.id}/files-added",
        json={"notify": True, "file_ids": [f1.id, f2.id, "bogus-id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    # Audit: count reflects only the 2 file_ids actually on this share.
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_files_added.value)
        .one()
    )
    assert audit.extra["count"] == 2
    assert audit.extra["notified"] is True
    # Recipient notified; owner is not.
    notifs = (
        db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_files_added)
        .all()
    )
    assert {n.user_id for n in notifs} == {recipient.id}
    assert notifs[0].payload_json["added_count"] == 2


@pytest.mark.asyncio
async def test_files_added_no_notify_still_audits(db, make_user, client, login_as):
    owner = make_user(email="owner@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="rcpt@test.local", role=UserRole.client)
    share = _make_share(db, owner)
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    f1 = _add_file(db, share, owner)
    db.commit()

    token, _ = await login_as("owner@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/shares/{share.id}/files-added",
        json={"notify": False, "file_ids": [f1.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_files_added.value)
        .count()
        == 1
    )
    assert (
        db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_files_added)
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_files_added_non_owner_forbidden(db, make_user, client, login_as):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    make_user(email="other@test.local", role=UserRole.employee, password="Pass12345678!")
    share = _make_share(db, owner)
    db.commit()
    token, _ = await login_as("other@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/shares/{share.id}/files-added",
        json={"notify": False, "file_ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_files_added_inactive_share_conflict(db, make_user, client, login_as):
    owner = make_user(email="owner@test.local", role=UserRole.employee, password="Pass12345678!")
    share = _make_share(db, owner, state=ShareState.revoked)
    db.commit()
    token, _ = await login_as("owner@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/shares/{share.id}/files-added",
        json={"notify": False, "file_ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "SHARE_NOT_ACTIVE"
