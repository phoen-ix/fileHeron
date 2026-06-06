"""Deleting the last non-deleted file from an active share auto-revokes it.

An empty active share is functionally useless - recipients see "no
files in this share", the owner can't add files later (no such API).
Auto-revoke on last-file-delete keeps the lifecycle honest. Audit
metadata `reason: last_file_deleted` distinguishes this path from
manual revoke or AV-triggered revoke.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed(db, sender, recipient, tmp_path, *, n: int = 2) -> tuple[Share, list[File]]:
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="t",
        message=None,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    files: list[File] = []
    for i in range(n):
        on_disk = tmp_path / f"f-{i}.bin"
        on_disk.write_bytes(b"x" * 16)
        f = File(
            id=f"file-uuid-{i}",
            share_id=share.id,
            original_filename=f"smoke-{i}.bin",
            mime_type="application/octet-stream",
            size_bytes=16,
            state=FileState.clean,
            storage_path=str(on_disk),
            uploaded_by_id=sender.id,
            finalized_at=_now_naive(),
        )
        db.add(f)
        files.append(f)
    db.commit()
    return share, files


@pytest.mark.asyncio
async def test_delete_last_file_revokes_share(
    make_user, db, client, login_as, tmp_path
):
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, files = _seed(db, sender, recipient, tmp_path, n=2)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Delete the first file - share stays active.
    r1 = await client.delete(f"/api/files/{files[0].id}", headers=headers)
    assert r1.status_code == 204
    db.expire_all()
    assert db.query(Share).filter(Share.id == share.id).one().state == ShareState.active

    # Delete the second - share auto-revokes.
    r2 = await client.delete(f"/api/files/{files[1].id}", headers=headers)
    assert r2.status_code == 204
    db.expire_all()
    s_after = db.query(Share).filter(Share.id == share.id).one()
    assert s_after.state == ShareState.revoked

    # Audit row carries the distinguishing reason.
    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.share_revoked.value,
            AuditLog.target_id == share.id,
        )
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.actor_user_id == sender.id
    assert audit.extra == {"reason": "last_file_deleted"}


@pytest.mark.asyncio
async def test_delete_when_other_files_remain_keeps_share_active(
    make_user, db, client, login_as, tmp_path
):
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, files = _seed(db, sender, recipient, tmp_path, n=3)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.delete(f"/api/files/{files[0].id}", headers=headers)
    assert r.status_code == 204
    db.expire_all()
    assert db.query(Share).filter(Share.id == share.id).one().state == ShareState.active

    # No share_revoked audit because the last file is still there.
    audits = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.share_revoked.value,
            AuditLog.target_id == share.id,
        )
        .all()
    )
    assert audits == []


@pytest.mark.asyncio
async def test_delete_in_already_revoked_share_does_not_re_audit(
    make_user, db, client, login_as, tmp_path
):
    """If the share was already revoked (e.g., AV quarantine, manual
    revoke), deleting a file shouldn't fire a second share_revoked
    audit row."""
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, files = _seed(db, sender, recipient, tmp_path, n=1)
    share.state = ShareState.revoked
    db.commit()
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.delete(f"/api/files/{files[0].id}", headers=headers)
    assert r.status_code == 204
    audits = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.share_revoked.value,
            AuditLog.target_id == share.id,
        )
        .all()
    )
    assert audits == []
