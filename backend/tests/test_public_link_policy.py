"""Public-link policy gate (post-Phase 10).

Mirrors test_api_token_policy.py - same policy shape, different verb.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import public_link as public_link_svc
from app.services import settings as settings_svc
from app.services import share as share_svc


def _set_policy(
    db,
    *,
    mode: str,
    user_ids: list[int] | None = None,
    group_ids: list[int] | None = None,
    actor=None,
):
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_POLICY_MODE,
        value=mode,
        actor=actor,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS,
        value=json.dumps(user_ids) if user_ids else None,
        actor=actor,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS,
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


def _future(days: int = 7) -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)


def test_default_mode_is_employees_admins(make_user, db):
    """Default is employees_admins (audit L27): a client can't create a public
    link out of the box; staff can."""
    client = make_user(email="c@test.local", role=UserRole.client)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    admin = make_user(email="a@test.local", role=UserRole.admin)
    assert public_link_svc.is_allowed_to_create(db, client) is False
    assert public_link_svc.is_allowed_to_create(db, employee) is True
    assert public_link_svc.is_allowed_to_create(db, admin) is True


def test_admins_only_blocks_clients_and_employees(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="admins_only")
    assert public_link_svc.is_allowed_to_create(db, admin) is True
    assert public_link_svc.is_allowed_to_create(db, employee) is False
    assert public_link_svc.is_allowed_to_create(db, client) is False


def test_employees_admins_blocks_clients(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="employees_admins")
    assert public_link_svc.is_allowed_to_create(db, employee) is True
    assert public_link_svc.is_allowed_to_create(db, client) is False


def test_disabled_mode_admin_escape_hatch(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    employee = make_user(email="e@test.local", role=UserRole.employee)
    _set_policy(db, mode="disabled")
    assert public_link_svc.is_allowed_to_create(db, admin) is True
    assert public_link_svc.is_allowed_to_create(db, employee) is False


def test_user_allowlist_overrides_base_mode(make_user, db):
    client = make_user(email="c@test.local", role=UserRole.client)
    _set_policy(db, mode="admins_only", user_ids=[client.id])
    assert public_link_svc.is_allowed_to_create(db, client) is True


def test_group_allowlist_via_membership(make_user, db):
    employee = make_user(email="e@test.local", role=UserRole.employee)
    client = make_user(email="c@test.local", role=UserRole.client)
    g = _make_group(db, "public-link-allowed")
    db.add(GroupMember(group_id=g.id, user_id=client.id))
    db.commit()

    _set_policy(db, mode="admins_only", group_ids=[g.id])
    assert public_link_svc.is_allowed_to_create(db, client) is True
    assert public_link_svc.is_allowed_to_create(db, employee) is False


@pytest.mark.asyncio
async def test_route_returns_403_when_policy_blocks(
    make_user, db, client, login_as
):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="x",
        message=None,
    )
    # Sign in as admin then tighten policy → admin still allowed (escape
    # hatch). To verify the route gate, sign in as the recipient (client)
    # who shouldn't be able to add a public link to their own share.
    # Easier: temporarily downgrade by setting mode=disabled and signing
    # in as a non-admin who owns a share.
    db.commit()

    # Switch to a client-owned share for the negative test.
    client_user = make_user(
        email="cc@test.local", role=UserRole.client, password="Pass12345678!"
    )
    employee = make_user(email="ee@test.local", role=UserRole.employee)
    # Connection so client can target employee in inbound share.
    from app.services.connection import record_invite_connection

    record_invite_connection(db, inviter=employee, invitee=client_user)
    db.commit()
    inbound = share_svc.create_share(
        db,
        created_by=client_user,
        kind=ShareKind.inbound,
        recipient_user_ids=[employee.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="hi",
        message=None,
    )
    db.commit()
    _set_policy(db, mode="admins_only")

    token, _ = await login_as("cc@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/shares/{inbound.id}/public-link",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PUBLIC_LINK_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_admin_only_endpoints_refuse_non_admins(
    make_user, client, login_as
):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    for method, path in (
        ("get", "/api/admin/settings/public-links/policy"),
        ("put", "/api/admin/settings/public-links/policy"),
    ):
        if method == "get":
            r = await client.get(
                path, headers={"Authorization": f"Bearer {token}"}
            )
        else:
            r = await client.put(
                path,
                json={
                    "mode": "everyone",
                    "allowed_user_ids": [],
                    "allowed_group_ids": [],
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_policy_get_put_round_trip(make_user, db, client, login_as):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    token, _ = await login_as("a@test.local", "Pass12345678!")

    r1 = await client.get(
        "/api/admin/settings/public-links/policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["mode"] == "employees_admins"  # default tightened (audit L27)

    r2 = await client.put(
        "/api/admin/settings/public-links/policy",
        json={
            "mode": "admins_only",
            "allowed_user_ids": [bob.id],
            "allowed_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    assert r2.json()["allowed_users"][0]["id"] == bob.id

    from app.models.audit_log import AuditEventType, AuditLog

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.public_link_policy_changed.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["mode"] == "admins_only"
    assert rows[0].extra["user_count"] == 1


@pytest.mark.asyncio
async def test_policy_put_rejects_unknown_user(make_user, client, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    r = await client.put(
        "/api/admin/settings/public-links/policy",
        json={
            "mode": "admins_only",
            "allowed_user_ids": [99999],
            "allowed_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "USER_NOT_FOUND"
