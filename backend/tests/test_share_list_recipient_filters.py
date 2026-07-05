"""Regression: the outbox list 500'd when BOTH recipient_user_id and
recipient_group_id filters were passed (two unaliased joins on ShareRecipient
collided). Correlated EXISTS per filter is unambiguous and composes (AND).
"""
from __future__ import annotations

from app.models.group import Group
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import share as share_svc


def _share(db, owner, *, user_rcpt=None, group_rcpt=None):
    share = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(share)
    db.flush()
    if user_rcpt is not None:
        db.add(ShareRecipient(share_id=share.id, recipient_user_id=user_rcpt))
    if group_rcpt is not None:
        db.add(ShareRecipient(share_id=share.id, recipient_group_id=group_rcpt))
    db.commit()
    return share


def test_outbox_list_with_both_recipient_filters(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    grp = Group(name="Team", name_normalized="team", created_by_id=owner.id)
    db.add(grp)
    db.flush()

    both = _share(db, owner, user_rcpt=recipient.id, group_rcpt=grp.id)
    # A share matching only ONE of the filters, to prove the AND semantics.
    _share(db, owner, user_rcpt=recipient.id)

    shares, total = share_svc.list_shares_for_user(
        db, user=owner, box="outbox",
        recipient_user_id=recipient.id, recipient_group_id=grp.id,
    )
    # No 500, and only the share with BOTH a user- and a group-recipient matches.
    assert total == 1
    assert [s.id for s in shares] == [both.id]
