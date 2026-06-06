"""PATCH /api/shares/{id} (editable expiry) + POST /{id}/expire
(force-expire-now) - post-Phase 10."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import ShareKind, ShareState
from app.models.user import UserRole
from app.services import share as share_svc


def _future(days: int = 7) -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)


def _make_share(db, sender, rec, *, days_ahead: int = 7):
    return share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(days_ahead),
        subject="x",
        message=None,
    )


def _make_file(db, *, share, uploader, name: str = "a.bin", size: int = 1024):
    f = File(
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=size,
        uploaded_by_id=uploader.id,
        state=FileState.clean,
        finalized_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
        storage_path=f"/tmp/fileheron-test/files/{share.id}-{name}",
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


# -------------------- PATCH expires_at --------------------


@pytest.mark.asyncio
async def test_owner_can_extend_expiry(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec, days_ahead=2)
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    new_iso = _future(30).isoformat()
    resp = await client.patch(
        f"/api/shares/{share.id}",
        json={"expires_at": new_iso},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db.refresh(share)
    assert share.expires_at > _future(20)

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_expiry_updated.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["new_expires_at"].startswith(new_iso[:10])


@pytest.mark.asyncio
async def test_patch_refuses_past_expiry(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec)
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    past = (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    ).isoformat()
    resp = await client.patch(
        f"/api/shares/{share.id}",
        json={"expires_at": past},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_EXPIRY"


@pytest.mark.asyncio
async def test_patch_refuses_non_owner(make_user, db, client, login_as):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    rec = make_user(
        email="r@test.local", role=UserRole.client, password="Pass12345678!"
    )
    share = _make_share(db, sender, rec)
    db.commit()
    token, _ = await login_as("r@test.local", "Pass12345678!")

    resp = await client.patch(
        f"/api/shares/{share.id}",
        json={"expires_at": _future(30).isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_refuses_revoked_share(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec)
    share.state = ShareState.revoked
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.patch(
        f"/api/shares/{share.id}",
        json={"expires_at": _future(14).isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "SHARE_NOT_ACTIVE"


# -------------------- POST /expire --------------------


@pytest.mark.asyncio
async def test_expire_now_deletes_files_and_flips_state(
    make_user, db, client, login_as, tmp_path
):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec)
    f1 = _make_file(db, share=share, uploader=sender, name="a.bin")
    f2 = _make_file(db, share=share, uploader=sender, name="b.bin")
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/shares/{share.id}/expire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db.refresh(share)
    db.refresh(f1)
    db.refresh(f2)
    assert share.state == ShareState.expired
    assert share.expires_at <= datetime.now(tz=timezone.utc).replace(tzinfo=None)
    assert f1.state == FileState.deleted
    assert f2.state == FileState.deleted

    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.share_expired.value,
            AuditLog.actor_user_id == sender.id,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["via"] == "owner_action"
    assert rows[0].extra["file_count"] == 2


@pytest.mark.asyncio
async def test_expire_now_refuses_when_already_inactive(
    make_user, db, client, login_as
):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec)
    share.state = ShareState.expired
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/shares/{share.id}/expire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "SHARE_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_expire_now_refuses_non_owner(
    make_user, db, client, login_as
):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    rec = make_user(
        email="r@test.local", role=UserRole.client, password="Pass12345678!"
    )
    share = _make_share(db, sender, rec)
    db.commit()
    token, _ = await login_as("r@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/shares/{share.id}/expire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expire_now_zero_files_succeeds(make_user, db, client, login_as):
    """A share with no files still flips state cleanly."""
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = _make_share(db, sender, rec)
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/shares/{share.id}/expire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    db.refresh(share)
    assert share.state == ShareState.expired
