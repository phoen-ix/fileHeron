"""Admin creates a user directly (no invite, email pre-verified, set password).

POST /api/admin/users — the "skip invite" path of the admin invite form.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import User, UserRole


def _make_group(db, name: str, creator_id: int) -> Group:
    g = Group(name=name, name_normalized=name.lower(), created_by_id=creator_id)
    db.add(g)
    db.commit()
    return g


async def _admin_headers(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin)
    token, _ = await login_as(email, "TestPassword123!")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_user_direct_can_login_immediately(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "Newbie@Test.Local",
            "display_name": "New Bie",
            "password": "FreshPassword123!",
            "target_role": "employee",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "newbie@test.local"  # normalized
    assert body["role"] == "employee"

    u = db.query(User).filter(User.email == "newbie@test.local").one()
    assert u.email_verified is True
    assert u.is_disabled is False

    # Pre-verified + password set → can log in right away, no invite step.
    login = await client.post(
        "/api/auth/login",
        json={"email": "newbie@test.local", "password": "FreshPassword123!"},
    )
    assert login.status_code == 200, login.text

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.user_created_by_admin.value)
        .one()
    )
    assert str(audit.target_id) == str(u.id)


@pytest.mark.asyncio
async def test_create_user_direct_applies_groups(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    admin = db.query(User).filter(User.email == "admin@test.local").one()
    g = _make_group(db, "Alpha", admin.id)

    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "grouped@test.local",
            "display_name": "Grouped",
            "password": "FreshPassword123!",
            "target_role": "client",
            "initial_group_ids": [g.id],
        },
    )
    assert r.status_code == 201, r.text
    u = db.query(User).filter(User.email == "grouped@test.local").one()
    members = db.query(GroupMember).filter(GroupMember.user_id == u.id).all()
    assert {m.group_id for m in members} == {g.id}


@pytest.mark.asyncio
async def test_create_user_direct_rejects_duplicate(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    make_user(email="taken@test.local", role=UserRole.client)
    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "taken@test.local",
            "display_name": "Dup",
            "password": "FreshPassword123!",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "USER_EXISTS"


@pytest.mark.asyncio
async def test_create_user_direct_rejects_breached_password(
    client, db, make_user, login_as, monkeypatch
):
    from app.services import hibp as hibp_svc

    async def _breached(_pw, _db=None):
        return True

    monkeypatch.setattr(hibp_svc, "is_password_breached", _breached)
    headers = await _admin_headers(make_user, login_as)
    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "breach@test.local",
            "display_name": "Breach",
            "password": "BreachedPassword123!",
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PASSWORD_BREACHED"


@pytest.mark.asyncio
async def test_create_user_direct_rejects_missing_group(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "nogroup@test.local",
            "display_name": "No Group",
            "password": "FreshPassword123!",
            "initial_group_ids": [99999],
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "GROUP_NOT_FOUND"
    assert body["details"]["missing_group_ids"] == [99999]


@pytest.mark.asyncio
async def test_create_user_direct_rejects_pending_invite(
    client, db, make_user, login_as
):
    headers = await _admin_headers(make_user, login_as)
    admin = db.query(User).filter(User.email == "admin@test.local").one()
    from app.services import invite as invite_svc

    invite_svc.create_invite(
        db, email="pending@test.local", target_role=UserRole.client, created_by=admin
    )
    db.commit()
    r = await client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "pending@test.local",
            "display_name": "Pending",
            "password": "FreshPassword123!",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "INVITE_PENDING"


@pytest.mark.asyncio
async def test_create_user_direct_requires_admin(client, db, make_user, login_as):
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")
    r = await client.post(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "x@test.local",
            "display_name": "X",
            "password": "FreshPassword123!",
        },
    )
    assert r.status_code == 403
