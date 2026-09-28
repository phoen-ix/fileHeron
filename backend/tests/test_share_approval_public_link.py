"""A public link counts as leaving the organisation under approval scope
`outbound_to_clients` (v2.21.0).

The scope asks "does this leave the organisation", and read only recipient
rows. A share whose only audience was a public link - readable by anyone who
holds the URL - went live unreviewed, because the link row is created AFTER
`is_approval_required` runs. And a link attached later to a live share that
had never been held needed no one's approval either.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import share as share_svc

PW = "Pass12345678!"
K = settings_svc.Keys


def _set(db, key: str, value: str) -> None:
    settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


def _enable_approval(db, *, scope: str = "outbound_to_clients") -> None:
    _set(db, K.SHARE_APPROVAL_ENABLED, "true")
    _set(db, K.SHARE_APPROVAL_APPROVER_MODE, "admins_only")
    _set(db, K.SHARE_APPROVAL_SCOPE, scope)
    _set(db, K.SHARE_APPROVAL_EXEMPT_APPROVERS, "true")


def _future_iso() -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=3)
    ).isoformat()


async def _hdr(login_as, email):
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_a_link_only_share_is_held(make_user, db, client, login_as):
    _enable_approval(db)
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [], "group_ids": []},
            "expires_at": _future_iso(),
            "public_link": {"password": None, "download_limit": None},
        },
        headers=await _hdr(login_as, "emp@test.local"),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "pending_approval", (
        "a share whose audience is a public link leaves the organisation"
    )


@pytest.mark.asyncio
async def test_an_internal_share_without_a_link_is_not_held(make_user, db, client, login_as):
    """Control: the scope still lets staff-to-staff shares through."""
    _enable_approval(db)
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    col = make_user(email="col@test.local", role=UserRole.employee, password=PW)

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [col.id], "group_ids": []},
            "expires_at": _future_iso(),
        },
        headers=await _hdr(login_as, "emp@test.local"),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "active"


def _live_internal_share(db, make_user) -> Share:
    sender = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    col = make_user(email="col@test.local", role=UserRole.employee, password=PW)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[col.id],
        expires_at=None,
    )
    db.commit()
    assert share.state == ShareState.active and not share.approval_was_required
    return share


async def _attach(client, hdr, share_id):
    return await client.post(
        f"/api/shares/{share_id}/public-link",
        json={"password": None, "download_limit": None},
        headers=hdr,
    )


@pytest.mark.asyncio
async def test_attaching_a_link_later_needs_an_admin(make_user, db, client, login_as):
    _enable_approval(db)
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    share = _live_internal_share(db, make_user)

    owner = await _attach(client, await _hdr(login_as, "emp@test.local"), share.id)
    assert owner.status_code == 409, owner.text
    assert owner.json()["code"] == "APPROVAL_REQUIRED"

    admin = await _attach(client, await _hdr(login_as, "admin@test.local"), share.id)
    assert admin.status_code == 201, admin.text


@pytest.mark.asyncio
async def test_without_approval_the_owner_attaches_as_before(make_user, db, client, login_as):
    """Control: nothing changes on an instance that does not use approval."""
    share = _live_internal_share(db, make_user)

    resp = await _attach(client, await _hdr(login_as, "emp@test.local"), share.id)

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_an_exempt_approver_attaches_to_their_own_share(make_user, db, client, login_as):
    """`exempt_approvers` auto-approves an approver's own shares; attaching a
    link later must honour the same exemption rather than refuse it."""
    _enable_approval(db)
    _set(db, K.SHARE_APPROVAL_APPROVER_MODE, "employees_admins")
    share = _live_internal_share(db, make_user)

    resp = await _attach(client, await _hdr(login_as, "emp@test.local"), share.id)

    assert resp.status_code == 201, resp.text
