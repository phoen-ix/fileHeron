"""2FA enforcement policy + admin editor (post-Phase 10)."""
from __future__ import annotations

import json

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import UserRole
from app.models.user_totp import UserTOTP
from app.services import settings as settings_svc
from app.services import twofa_policy as twofa_policy_svc


# ---- helpers --------------------------------------------------------------


def _set_kv_policy(
    db,
    *,
    roles: list[str] | None = None,
    group_ids: list[int] | None = None,
    actor=None,
):
    """Persist directly via settings_svc (mirrors what the route does)."""
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.TWOFA_REQUIRED_ROLES,
        value=json.dumps(roles) if roles is not None else None,
        actor=actor,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.TWOFA_REQUIRED_GROUPS,
        value=json.dumps(group_ids) if group_ids is not None else None,
        actor=actor,
    )
    db.commit()


def _make_group(db, name: str, created_by_id: int) -> Group:
    g = Group(
        name=name,
        name_normalized=name.lower(),
        is_company_inbox=False,
        created_by_id=created_by_id,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def _enable_totp(db, user_id: int):
    """Pretend the user already enrolled TOTP."""
    from datetime import datetime, timezone
    row = UserTOTP(
        user_id=user_id,
        secret_encrypted=b"dummy",
        enabled_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
        last_used_counter=0,
    )
    db.add(row)
    db.commit()


def _issue_token(user_id: int) -> str:
    """Mint an access token directly. Used to skip the login flow when
    the user has TOTP enrolled (the test login fixture doesn't supply
    a TOTP code, so logging in would 401)."""
    from app.config import settings as app_settings
    from app.services.auth import create_access_token

    token, _exp = create_access_token(user_id, app_settings)
    return token


# ---- env-fallback resolution ---------------------------------------------


def test_default_inherits_env(make_user, db, monkeypatch):
    """No kv keys set → REQUIRE_2FA env determines roles."""
    from app.config import settings as app_settings

    admin = make_user(email="a@test.local", role=UserRole.admin)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "admins")
    assert twofa_policy_svc.is_2fa_required(db, admin) is True
    assert twofa_policy_svc.is_2fa_required(db, employee) is False
    assert twofa_policy_svc.is_2fa_required(db, client) is False

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "all")
    assert twofa_policy_svc.is_2fa_required(db, admin) is True
    assert twofa_policy_svc.is_2fa_required(db, employee) is True
    assert twofa_policy_svc.is_2fa_required(db, client) is True

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "none")
    assert twofa_policy_svc.is_2fa_required(db, admin) is False
    assert twofa_policy_svc.is_2fa_required(db, employee) is False


def test_kv_overrides_env(make_user, db, monkeypatch):
    """Once kv keys are set, env is ignored even if it's stricter."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "all")

    admin = make_user(email="a@test.local", role=UserRole.admin)
    _set_kv_policy(db, roles=[], group_ids=[])

    # kv override wins → no enforcement
    assert twofa_policy_svc.is_2fa_required(db, admin) is False


# ---- per-role enforcement -------------------------------------------------


def test_role_required_blocks_matching_role(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    admin = make_user(email="a@test.local", role=UserRole.admin)

    _set_kv_policy(db, roles=["employee"])

    assert twofa_policy_svc.is_2fa_required(db, employee) is True
    assert twofa_policy_svc.is_2fa_required(db, client) is False
    assert twofa_policy_svc.is_2fa_required(db, admin) is False


def test_user_with_totp_never_required(make_user, db):
    """Whatever the policy says, an enrolled user is fine."""
    admin = make_user(email="a@test.local", role=UserRole.admin)
    _set_kv_policy(db, roles=["admin", "employee", "client"])
    _enable_totp(db, admin.id)
    db.refresh(admin)

    assert twofa_policy_svc.is_2fa_required(db, admin) is False


# ---- per-group enforcement -----------------------------------------------


def test_group_membership_triggers_requirement(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    admin = make_user(email="a@test.local", role=UserRole.admin)
    g = _make_group(db, "high-risk", created_by_id=admin.id)
    db.add(GroupMember(group_id=g.id, user_id=client.id))
    db.commit()

    _set_kv_policy(db, roles=[], group_ids=[g.id])

    assert twofa_policy_svc.is_2fa_required(db, client) is True   # member
    assert twofa_policy_svc.is_2fa_required(db, employee) is False  # not member


# ---- admin editor endpoints ----------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_resolved_shape(
    make_user, db, client, login_as
):
    admin = make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    # Admin needs TOTP enrolled to pass the gate before they can view
    # the policy editor — the editor UI warns about this when saving.
    _enable_totp(db, admin.id)
    g = _make_group(db, "ops", created_by_id=admin.id)
    _set_kv_policy(db, roles=["admin"], group_ids=[g.id])

    token = _issue_token(admin.id)
    resp = await client.get(
        "/api/admin/settings/twofa",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["required_roles"] == ["admin"]
    assert body["required_group_ids"] == [g.id]
    assert body["required_groups"] == [
        {"id": g.id, "name": "ops", "is_company_inbox": False}
    ]
    assert body["is_kv_overridden"] is True


@pytest.mark.asyncio
async def test_put_validates_role_names(
    make_user, db, client
):
    admin = make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    _enable_totp(db, admin.id)
    token = _issue_token(admin.id)
    resp = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": ["bogus"], "required_group_ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_ROLE"


@pytest.mark.asyncio
async def test_put_validates_group_ids(
    make_user, db, client
):
    admin = make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    _enable_totp(db, admin.id)
    token = _issue_token(admin.id)
    resp = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": [], "required_group_ids": [9999]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_put_persists_and_audits(
    make_user, db, client
):
    admin = make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    _enable_totp(db, admin.id)
    g = _make_group(db, "auditors", created_by_id=admin.id)
    token = _issue_token(admin.id)

    resp = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": ["admin", "employee"], "required_group_ids": [g.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # Round-trip through the resolver
    roles, group_ids, is_kv_overridden = twofa_policy_svc._resolve_policy(db)
    assert roles == {"admin", "employee"}
    assert group_ids == [g.id]
    assert is_kv_overridden is True

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.twofa_policy_changed.value)
        .one()
    )
    # Counts only — no IDs leaked into the audit metadata.
    assert audit.extra == {"role_count": 2, "group_count": 1}


# ---- /me + AdminUserItem reflect the policy live -------------------------


@pytest.mark.asyncio
async def test_me_response_reflects_requirement(
    make_user, db, client, login_as
):
    """When the policy applies and user has no TOTP, /me.requires_2fa
    is true. After enrolling, it flips false on the next /me hit."""
    make_user(email="e@test.local", role=UserRole.employee, password="Pass12345678!")
    _set_kv_policy(db, roles=["employee"])

    token, _ = await login_as("e@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["requires_2fa"] is True

    # Simulate the user finishing setup — flip to false on next read.
    user = (
        db.query(__import__("app.models.user", fromlist=["User"]).User)
        .filter_by(email="e@test.local")
        .one()
    )
    _enable_totp(db, user.id)

    resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["requires_2fa"] is False


# ---- the gate actually blocks privileged routes --------------------------


@pytest.mark.asyncio
async def test_gate_blocks_protected_route(
    make_user, db, client, login_as
):
    """Admin route should refuse with TWOFA_SETUP_REQUIRED for an
    admin who hasn't enrolled when the policy requires admins."""
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    _set_kv_policy(db, roles=["admin"])

    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "TWOFA_SETUP_REQUIRED"


@pytest.mark.asyncio
async def test_gate_does_not_block_account_2fa_setup(
    make_user, db, client, login_as
):
    """The user must be able to reach /api/account/2fa/* to actually
    enable TOTP and clear the requirement."""
    make_user(email="e@test.local", role=UserRole.employee, password="Pass12345678!")
    _set_kv_policy(db, roles=["employee"])
    token, _ = await login_as("e@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/account/2fa/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 200 = endpoint reachable. The gate would have returned 403.
    assert resp.status_code == 200
