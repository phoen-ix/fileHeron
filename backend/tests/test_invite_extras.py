"""Extended invite behavior: duplicate-check + initial_group_ids.

Covers:
- POST /api/account/invite refuses 409 USER_EXISTS when an account already
  exists for the email.
- POST /api/account/invite refuses 409 INVITE_PENDING when an unused,
  unexpired invite exists for the email.
- Initial group IDs are validated; unknown IDs → 400 GROUP_NOT_FOUND.
- After invite consume, the invitee is a member of the pre-assigned groups.
- has_pending_invite returns False for used/expired invites.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import UserRole
from app.services import auth as auth_svc
from app.services import invite as invite_svc


def _make_group(db, name: str, *, is_inbox: bool = False) -> Group:
    g = Group(
        name=name,
        name_normalized=name.lower(),
        is_company_inbox=is_inbox,
        created_by_id=1,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


@pytest.mark.asyncio
async def test_invite_refuses_when_email_already_registered(
    make_user, client, login_as
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    make_user(email="taken@test.local", role=UserRole.client)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/account/invite",
        json={
            "email": "taken@test.local",
            "display_name_hint": "Already In",
            "target_role": "client",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "USER_EXISTS"


@pytest.mark.asyncio
async def test_invite_refuses_when_pending_invite_exists(
    make_user, client, login_as, db
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    invite_svc.create_invite(
        db,
        email="pending@test.local",
        target_role=UserRole.client,
        created_by=admin,
    )
    db.commit()
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/account/invite",
        json={
            "email": "pending@test.local",
            "display_name_hint": "Re-invite",
            "target_role": "client",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "INVITE_PENDING"


@pytest.mark.asyncio
async def test_invite_validates_group_ids(make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/account/invite",
        json={
            "email": "fresh@test.local",
            "display_name_hint": "Fresh",
            "target_role": "client",
            "initial_group_ids": [9999],  # doesn't exist
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "GROUP_NOT_FOUND"
    assert 9999 in body["details"]["missing_group_ids"]


@pytest.mark.asyncio
async def test_invite_with_groups_applies_memberships_on_consume(
    make_user, client, login_as, db
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    g_alpha = _make_group(db, "alpha")
    g_beta = _make_group(db, "beta")

    # Use the service directly (so we can grab the plaintext token).
    record, plaintext = invite_svc.create_invite(
        db,
        email="member@test.local",
        target_role=UserRole.client,
        created_by=admin,
        initial_group_ids=[g_alpha.id, g_beta.id],
    )
    db.commit()

    # Consume → the new user gets both group memberships.
    user = await auth_svc.register_from_invite(
        db,
        plaintext_token=plaintext,
        password="Pass12345678!",
        display_name="Member",
        locale=admin.locale,
        request=None,
    )
    db.commit()

    members = (
        db.query(GroupMember).filter(GroupMember.user_id == user.id).all()
    )
    assert {m.group_id for m in members} == {g_alpha.id, g_beta.id}


@pytest.mark.asyncio
async def test_invite_with_deleted_group_silently_skips(
    make_user, client, login_as, db
):
    """If a group is deleted between invite creation and consume, the
    consume succeeds and just skips the missing group rather than
    failing. Defensive — better UX for the invitee."""
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    g = _make_group(db, "soon-gone")

    record, plaintext = invite_svc.create_invite(
        db,
        email="ghost@test.local",
        target_role=UserRole.client,
        created_by=admin,
        initial_group_ids=[g.id],
    )
    db.commit()

    # Delete the group AFTER the invite is created.
    db.delete(g)
    db.commit()

    user = await auth_svc.register_from_invite(
        db,
        plaintext_token=plaintext,
        password="Pass12345678!",
        display_name="Ghost",
        locale=admin.locale,
        request=None,
    )
    db.commit()

    members = (
        db.query(GroupMember).filter(GroupMember.user_id == user.id).all()
    )
    assert members == []


def test_has_pending_invite_returns_false_for_used(make_user, db):
    admin = make_user(role=UserRole.admin)
    record, _ = invite_svc.create_invite(
        db, email="x@test.local", target_role=UserRole.client, created_by=admin
    )
    record.used_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    db.commit()

    from app.utils.crypto import normalize_email

    assert (
        invite_svc.has_pending_invite(db, email_value=normalize_email("x@test.local"))
        is False
    )


def test_has_pending_invite_returns_false_for_expired(make_user, db):
    admin = make_user(role=UserRole.admin)
    record, _ = invite_svc.create_invite(
        db, email="y@test.local", target_role=UserRole.client, created_by=admin
    )
    record.expires_at = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(
        hours=1
    )
    db.commit()

    from app.utils.crypto import normalize_email

    assert (
        invite_svc.has_pending_invite(db, email_value=normalize_email("y@test.local"))
        is False
    )
