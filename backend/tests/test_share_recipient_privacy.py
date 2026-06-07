"""M4: a plain share recipient must not be able to enumerate the full
co-recipient list via the share-detail serializer; the creator/admin still sees
everyone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
