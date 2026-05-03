"""API token policy gate (post-Phase 10).

`is_allowed_to_create` matrix across modes × roles × allowlists +
the route-level 403 surfacing through `POST /api/account/api-tokens`.
"""
from __future__ import annotations

import json

import pytest

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import UserRole
from app.services import api_token as api_token_svc
from app.services import settings as settings_svc


def _set_policy(
    db,
    *,
    mode: str,
    user_ids: list[int] | None = None,
    group_ids: list[int] | None = None,
    actor=None,
):
    settings_svc.set_value(
        db, key=settings_svc.Keys.API_TOKEN_POLICY_MODE, value=mode, actor=actor
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_USERS,
        value=json.dumps(user_ids) if user_ids else None,
        actor=actor,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS,
        value=json.dumps(group_ids) if group_ids else None,
        actor=actor,
    )
    db.commit()


def _make_group(db, name: str) -> Group:
    g = Group(
        name=name,
        name_normalized=name.lower(),
        is_company_inbox=False,
        created_by_id=1,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def test_default_mode_allows_everyone(make_user, db):
    """No policy rows yet → defaults to `everyone`."""
    client = make_user(email="c@test.local", role=UserRole.client)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    admin = make_user(email="a@test.local", role=UserRole.admin)
    assert api_token_svc.is_allowed_to_create(db, client) is True
    assert api_token_svc.is_allowed_to_create(db, employee) is True
    assert api_token_svc.is_allowed_to_create(db, admin) is True


def test_admins_only_blocks_clients_and_employees(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="admins_only")

    assert api_token_svc.is_allowed_to_create(db, admin) is True
    assert api_token_svc.is_allowed_to_create(db, employee) is False
    assert api_token_svc.is_allowed_to_create(db, client) is False


def test_employees_admins_blocks_clients(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="employees_admins")

    assert api_token_svc.is_allowed_to_create(db, employee) is True
    assert api_token_svc.is_allowed_to_create(db, client) is False


def test_disabled_blocks_everyone_except_admin_escape_hatch(make_user, db):
    """Mode=disabled means no token creation by anyone — except admin
    keeps an escape hatch (operator should always be able to create one)."""
    admin = make_user(email="a@test.local", role=UserRole.admin)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="disabled")

    assert api_token_svc.is_allowed_to_create(db, admin) is True
    assert api_token_svc.is_allowed_to_create(db, employee) is False
    assert api_token_svc.is_allowed_to_create(db, client) is False


def test_user_allowlist_overrides_base_mode(make_user, db):
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="admins_only", user_ids=[client.id])

    assert api_token_svc.is_allowed_to_create(db, client) is True


def test_group_allowlist_via_membership(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    g = _make_group(db, "api-power-users")
    db.add(GroupMember(group_id=g.id, user_id=client.id))
    db.commit()

    _set_policy(db, mode="admins_only", group_ids=[g.id])

    # client is a member → allowed
    assert api_token_svc.is_allowed_to_create(db, client) is True
    # employee not a member → not allowed under admins_only base
    assert api_token_svc.is_allowed_to_create(db, employee) is False


@pytest.mark.asyncio
async def test_route_returns_403_when_policy_blocks(
    make_user, db, client, login_as
):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    _set_policy(db, mode="admins_only")
    token, _ = await login_as("c@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "API_TOKEN_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_list_response_includes_can_create(
    make_user, db, client, login_as
):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    _set_policy(db, mode="admins_only")
    token, _ = await login_as("c@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/account/api-tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["can_create"] is False

    # Now flip policy to everyone → can_create flips True without restart
    _set_policy(db, mode="everyone")
    resp = await client.get(
        "/api/account/api-tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["can_create"] is True
