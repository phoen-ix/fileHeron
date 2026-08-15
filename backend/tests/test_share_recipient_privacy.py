"""M4: a plain share recipient must not be able to enumerate the full
co-recipient list via the share-detail serializer; the creator/admin still sees
everyone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.routers.shares import _to_share_response


def _future():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def test_recipient_only_sees_self_creator_sees_all(make_user, db):
    creator = make_user(email="hr@test.local", role=UserRole.admin)
    a = make_user(email="a@test.local", role=UserRole.client)
    b = make_user(email="b@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=_future(),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    # Recipient A sees only their own id - not B's.
    resp_a = _to_share_response(db, share, viewer=a)
    assert resp_a.recipient_user_ids == [a.id]

    # The creator (and admins) still see the full roster.
    resp_creator = _to_share_response(db, share, viewer=creator)
    assert set(resp_creator.recipient_user_ids) == {a.id, b.id}


# --- the same rule on the LIST route ----------------------------------------
#
# The projection existed only on the detail serializer. GET /api/shares?box=inbox
# built its recipient refs from every ShareRecipient row with no viewer test, so
# any recipient could read the display name and role of every co-recipient plus
# the name of every group the share was addressed to - strictly MORE than the
# detail route discloses even to a fully privileged viewer, which exposes only
# user ids.


@pytest.mark.asyncio
async def test_the_inbox_list_does_not_leak_co_recipients(make_user, db, client, login_as):
    creator = make_user(email="hr2@test.local", role=UserRole.admin)
    a = make_user(email="a2@test.local", role=UserRole.client, password="Pass12345678!")
    b = make_user(email="b2@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id, kind=ShareKind.outbound, subject="s",
        message=None, expires_at=_future(), state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    token, _ = await login_as("a2@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares?box=inbox", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.text

    assert "b2@test.local" not in body
    ids = [
        ref["id"]
        for item in r.json()["items"]
        for ref in item.get("recipients", [])
        if ref.get("kind") == "user"
    ]
    assert b.id not in ids, "a co-recipient's identity leaked through the list route"
    assert ids == [a.id]


@pytest.mark.asyncio
async def test_the_creator_still_sees_the_full_roster_in_the_list(
    make_user, db, client, login_as
):
    """The projection must not blind the people who are supposed to see it."""
    creator = make_user(email="hr3@test.local", role=UserRole.employee, password="Pass12345678!")
    a = make_user(email="a3@test.local", role=UserRole.client)
    b = make_user(email="b3@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id, kind=ShareKind.outbound, subject="s",
        message=None, expires_at=_future(), state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    token, _ = await login_as("hr3@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares?box=outbox", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    ids = {
        ref["id"]
        for item in r.json()["items"]
        for ref in item.get("recipients", [])
        if ref.get("kind") == "user"
    }
    assert ids == {a.id, b.id}
