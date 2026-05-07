"""Admin user management endpoints + erasure."""
from __future__ import annotations

import pytest

from app.models.api_token import ApiToken
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_admin_list_users_filters(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    make_user(email="alice@test.local", role=UserRole.client, display_name="Alice")
    make_user(email="bob@test.local", role=UserRole.employee, display_name="Bob")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/admin/users?q=alice", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(u["display_name"] == "Alice" for u in items)
    assert all("Bob" not in u["display_name"] for u in items)


@pytest.mark.asyncio
async def test_admin_patch_user_role_audits(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    target = make_user(email="t@test.local", role=UserRole.client)
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.patch(
        f"/api/admin/users/{target.id}",
        json={"role": "employee"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "employee"
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.role_changed.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == admin.id


@pytest.mark.asyncio
async def test_admin_cannot_disable_self(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.patch(
        f"/api/admin/users/{admin.id}",
        json={"is_disabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "CANNOT_DISABLE_SELF"


@pytest.mark.asyncio
async def test_force_password_reset_returns_token(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    target = make_user(email="t@test.local", role=UserRole.client)
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/admin/users/{target.id}/force-password-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["plaintext_token"]) > 30


@pytest.mark.asyncio
async def test_erase_user_hard_deletes_files_and_anonymizes(
    make_user, db, client, login_as, tmp_path
):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    target = make_user(email="t@test.local", role=UserRole.client)

    # Seed an uploaded file (no share needed for the erasure walk —
    # it queries by uploaded_by_id directly).
    on_disk = tmp_path / "file.bin"
    on_disk.write_bytes(b"x" * 100)
    from app.models.share import Share, ShareKind, ShareState
    from datetime import datetime, timedelta, timezone

    share = Share(
        created_by_id=admin.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).replace(
            tzinfo=None
        ),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    f = File(
        id="erasure-file",
        share_id=share.id,
        original_filename="x.bin",
        mime_type="application/octet-stream",
        size_bytes=100,
        state=FileState.ready_unscanned,
        storage_path=str(on_disk),
        uploaded_by_id=target.id,
    )
    db.add(f)
    db.commit()

    # Also seed a refresh + an api token for the target so we can prove
    # they get wiped.
    db.add(
        ApiToken(
            owner_user_id=target.id,
            name="for-erasure-test",
            prefix="abc12345",
            secret_hash="deadbeef",
            last4="abcd",
        )
    )
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/admin/users/{target.id}/erase",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_files"] == 1
    assert body["deleted_bytes"] == 100

    # File on disk gone, file row marked deleted, user anonymized.
    assert not on_disk.exists()
    db.expire_all()
    f_after = db.query(File).filter(File.id == f.id).one()
    assert f_after.state == FileState.deleted

    from app.models.user import User
    t_after = db.query(User).filter(User.id == target.id).one()
    assert t_after.email == f"erased-{target.id}@erased.invalid"
    assert t_after.display_name == "[erased]"
    assert t_after.password_hash == ""
    assert t_after.is_disabled is True

    # API token gone.
    assert (
        db.query(ApiToken).filter(ApiToken.owner_user_id == target.id).count() == 0
    )

    # Audit row present with admin as actor.
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.user_erased.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == admin.id


@pytest.mark.asyncio
async def test_admin_erase_self_refused(make_user, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/admin/users/{admin.id}/erase",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "CANNOT_ERASE_SELF"
