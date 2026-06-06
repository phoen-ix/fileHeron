"""Invariant (v1.6.0 model): a client's share always goes to **the company**
and can never reach another client.

Clients no longer pick recipients. The server forces `kind` from role
(client → inbound, staff → outbound) and inbound shares store **no** recipient
rows - the audience (all staff + the creator's group-peers) is resolved at read
time. So even a hand-crafted payload naming another client as a recipient can't
leak: the recipients are ignored entirely.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import Share, ShareKind
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import connection as connection_svc


def _future_iso(days: int = 1) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    ).isoformat()


@pytest.mark.asyncio
async def test_client_share_forced_inbound_and_recipients_ignored(
    make_user, db, client, login_as
):
    make_user(email="bob@test.local", role=UserRole.client, password="Pass12345678!")
    other_client = make_user(email="charlie@test.local", role=UserRole.client)
    token, _ = await login_as("bob@test.local", "Pass12345678!")

    # Hostile payload: claims outbound + names another client as recipient.
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [other_client.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "to the company",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    row = db.query(Share).filter(Share.id == sid).one()
    assert row.kind == ShareKind.inbound  # forced inbound despite payload
    # No recipient rows at all → nothing points at the other client.
    rec_count = (
        db.query(ShareRecipient).filter(ShareRecipient.share_id == sid).count()
    )
    assert rec_count == 0


@pytest.mark.asyncio
async def test_employee_share_forced_outbound(make_user, db, client, login_as):
    emp = make_user(
        email="alice@test.local", role=UserRole.employee, password="Pass12345678!"
    )
    cl = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=emp, invitee=cl)
    db.commit()
    token, _ = await login_as("alice@test.local", "Pass12345678!")

    # Employee posts kind=inbound - server forces outbound (staff direction).
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "inbound",
            "recipients": {"user_ids": [cl.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "to a client",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    row = db.query(Share).filter(Share.id == resp.json()["id"]).one()
    assert row.kind == ShareKind.outbound
