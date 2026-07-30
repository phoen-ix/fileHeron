"""Invite authority: an employee may not grant a role or a group.

Regression cover for two 2026-07-30 audit findings on POST /api/account/invite:

1. `target_role` rode untouched from the request body through
   invite_svc.create_invite -> InviteToken.target_role -> User(role=...) at
   consume time. The route's only authority check was "is the caller an admin
   OR an employee", so any employee could invite an address they control with
   target_role=admin and hand themselves the admin shell.
2. `initial_group_ids` was checked for EXISTENCE only. Membership is resolved
   live by share.is_authorized_to_download, so seeding a group handed the new
   account every active share targeted at that group - while adding members is
   otherwise admin-only, and the send side already refuses an employee
   targeting a group they don't belong to.
"""
from __future__ import annotations

import pytest

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.invite_token import InviteToken
from app.models.user import UserRole


@pytest.fixture
def group(db, make_user):
    creator = make_user(email="group-owner@test.local", role=UserRole.admin)

    def _make(name: str = "Finance") -> Group:
        g = Group(
            name=name,
            name_normalized=name.strip().lower(),
            created_by_id=creator.id,
        )
        db.add(g)
        db.commit()
        return g

    return _make


async def _invite(client, token: str, **body):
    payload = {"email": "invitee@test.local", "display_name_hint": "Invitee"}
    payload.update(body)
    return await client.post(
        "/api/account/invite",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "employee"])
async def test_employee_cannot_invite_privileged_role(
    client, db, make_user, login_as, role
):
    """The escalation itself: employee -> admin (or -> employee)."""
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await _invite(client, token, target_role=role)

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVITE_ROLE_NOT_ALLOWED"
    # And nothing was persisted.
    assert db.query(InviteToken).count() == 0


@pytest.mark.asyncio
async def test_employee_can_still_invite_a_client(client, db, make_user, login_as):
    """Control: the normal employee workflow must keep working."""
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await _invite(client, token, target_role="client")

    assert resp.status_code == 201, resp.text
    invite = db.query(InviteToken).one()
    assert invite.target_role == UserRole.client


@pytest.mark.asyncio
async def test_admin_may_still_invite_an_admin(client, db, make_user, login_as):
    """Control: the clamp applies to non-admins only."""
    make_user(email="boss@test.local", role=UserRole.admin)
    token, _ = await login_as("boss@test.local", "TestPassword123!")

    resp = await _invite(client, token, target_role="admin")

    assert resp.status_code == 201, resp.text
    assert db.query(InviteToken).one().target_role == UserRole.admin


@pytest.mark.asyncio
async def test_employee_cannot_seed_a_group_they_are_not_in(
    client, db, make_user, login_as, group
):
    make_user(email="emp@test.local", role=UserRole.employee)
    g = group("Finance")
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await _invite(client, token, initial_group_ids=[g.id])

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "GROUP_NOT_MEMBER"
    assert db.query(InviteToken).count() == 0


@pytest.mark.asyncio
async def test_employee_can_seed_a_group_they_belong_to(
    client, db, make_user, login_as, group
):
    """Control: the legitimate case stays allowed."""
    emp = make_user(email="emp@test.local", role=UserRole.employee)
    g = group("Finance")
    db.add(GroupMember(group_id=g.id, user_id=emp.id))
    db.commit()
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await _invite(client, token, initial_group_ids=[g.id])

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_admin_may_seed_any_group(client, db, make_user, login_as, group):
    make_user(email="boss@test.local", role=UserRole.admin)
    g = group("Finance")
    token, _ = await login_as("boss@test.local", "TestPassword123!")

    resp = await _invite(client, token, initial_group_ids=[g.id])

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_nonexistent_group_still_reports_not_found(
    client, make_user, login_as
):
    """The authority check must not mask the existing 400 for a bad id."""
    make_user(email="boss@test.local", role=UserRole.admin)
    token, _ = await login_as("boss@test.local", "TestPassword123!")

    resp = await _invite(client, token, initial_group_ids=[999999])

    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "GROUP_NOT_FOUND"
