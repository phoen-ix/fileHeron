"""Invariant: a client must NEVER be able to send a share to another client.

The share system enforces this via several layers (`kind`-vs-role gate, the
connection-table classifier, and the recipient role check inside
`_validate_inbound_targets`). These tests pin down every variant — dropping
this file should break visibly if any layer regresses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import UserRole
from app.services import connection as connection_svc
from app.services import group as group_svc


def _future_iso(days: int = 1) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    ).isoformat()


@pytest.mark.asyncio
async def test_client_cannot_send_outbound(make_user, db, client, login_as):
    """Clients are senders of inbound shares only — outbound is for
    employees/admins. The kind-vs-role gate at the top of
    `_validate_outbound_targets` rejects clients with FORBIDDEN_KIND."""
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    recipient = make_user(email="alice@test.local", role=UserRole.employee)
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [recipient.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "should be rejected",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_KIND"


@pytest.mark.asyncio
async def test_employee_cannot_send_inbound(make_user, db, client, login_as):
    """Symmetric: only clients can send inbound. Employees trying to use
    inbound get FORBIDDEN_KIND."""
    sender = make_user(
        email="alice@test.local",
        role=UserRole.employee,
        password="Pass12345678!",
    )
    recipient = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(
        db, inviter=sender, invitee=recipient
    )
    db.commit()
    token, _ = await login_as("alice@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [recipient.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "wrong direction",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_KIND"


@pytest.mark.asyncio
async def test_client_cannot_target_another_client_no_connection(
    make_user, db, client, login_as
):
    """The classic case: client A directly tries to share with client B.
    No connection exists (and never could — the classifier refuses to
    create client↔client rows). The role check fires first and returns
    FORBIDDEN_RECIPIENT."""
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    other_client = make_user(email="charlie@test.local", role=UserRole.client)
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [other_client.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "should be rejected",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_RECIPIENT"


@pytest.mark.asyncio
async def test_client_cannot_target_demoted_employee_with_stale_connection(
    make_user, db, client, login_as
):
    """Edge case the role-check guard exists for: an employee with an
    invite-source connection to a client gets demoted to client. The
    connection row persists (`update_user` doesn't clean it up). Without
    the role check, the validator would let the share through because
    the demoted user's id is still in `_connected_employee_ids_of(...)`.
    With the role check, the recipient's current role wins."""
    formerly_employee = make_user(
        email="alice@test.local", role=UserRole.employee
    )
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    connection_svc.record_invite_connection(
        db, inviter=formerly_employee, invitee=sender
    )
    # Demote the employee to client without cleaning up connections —
    # this mirrors what `services/user_management.py::update_user`
    # currently does.
    formerly_employee.role = UserRole.client
    db.commit()

    # Sanity: the stale connection row really is still there, otherwise
    # this test would pass for the wrong reason.
    from app.services.share import _connected_employee_ids_of

    assert formerly_employee.id in _connected_employee_ids_of(db, sender.id)

    token, _ = await login_as("bob@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {
                "user_ids": [formerly_employee.id],
                "group_ids": [],
            },
            "expires_at": _future_iso(),
            "subject": "should be rejected",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_RECIPIENT"


@pytest.mark.asyncio
async def test_client_cannot_target_unconnected_employee(
    make_user, db, client, login_as
):
    """Regression-protection that the role check didn't shadow the
    pre-existing connection check. A client targeting an employee they're
    not connected to should still produce RECIPIENT_NOT_CONNECTED."""
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    stranger = make_user(email="alice@test.local", role=UserRole.employee)
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [stranger.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "no connection",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "RECIPIENT_NOT_CONNECTED"


@pytest.mark.asyncio
async def test_client_cannot_target_non_inbox_group(
    make_user, db, client, login_as
):
    """Group recipients of inbound shares must have is_company_inbox=True.
    A regular team group should be rejected even if the client somehow
    obtained the id."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    g = group_svc.create_group(
        db,
        actor=admin,
        name="internal team",
        description=None,
        is_company_inbox=False,
    )
    db.commit()
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [], "group_ids": [g.id]},
            "expires_at": _future_iso(),
            "subject": "leakage attempt",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "GROUP_NOT_INBOX"


@pytest.mark.asyncio
async def test_client_can_still_share_with_company_inbox_group(
    make_user, db, client, login_as
):
    """Happy path smoke: the role guard didn't break the legitimate
    client→company-inbox flow. Client targets an `is_company_inbox=True`
    group → 201 Created."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(
        email="bob@test.local", role=UserRole.client, password="Pass12345678!"
    )
    inbox = group_svc.create_group(
        db,
        actor=admin,
        name="incoming-from-clients",
        description=None,
        is_company_inbox=True,
    )
    db.commit()
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [], "group_ids": [inbox.id]},
            "expires_at": _future_iso(),
            "subject": "legit",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
