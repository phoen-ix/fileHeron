"""Dynamic group-membership share resolution.

A share targeting a group is visible to whoever is currently a member at
query time. Removing membership immediately revokes future access.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import group as group_svc
from app.services import share as share_svc


def _future() -> datetime:
    return (datetime.now(tz=timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_group_share_visible_to_current_members(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="a@test.local", role=UserRole.client)
    bob = make_user(email="b@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="inbox", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=alice)
    group_svc.add_member(db, actor=admin, group=g, user=bob)
    db.commit()

    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    db.commit()

    assert share_svc.is_authorized_to_download(db, user=alice, share=share) is True
    assert share_svc.is_authorized_to_download(db, user=bob, share=share) is True


@pytest.mark.asyncio
async def test_group_share_inbox_listing_dynamic(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="a@test.local", role=UserRole.client)
    bob = make_user(email="b@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="inbox", description=None, is_company_inbox=True
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=alice)
    db.commit()

    share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    db.commit()

    # Alice (member) sees it; Bob (not member) doesn't.
    assert share_svc.list_shares_for_user(db, user=alice, box="inbox")[1] == 1
    assert share_svc.list_shares_for_user(db, user=bob, box="inbox")[1] == 0

    # Add Bob → he sees it now.
    group_svc.add_member(db, actor=admin, group=g, user=bob)
    db.commit()
    assert share_svc.list_shares_for_user(db, user=bob, box="inbox")[1] == 1

    # Remove Alice → she stops seeing it.
    group_svc.remove_member(db, actor=admin, group=g, user=alice)
    db.commit()
    assert share_svc.list_shares_for_user(db, user=alice, box="inbox")[1] == 0


@pytest.mark.asyncio
async def test_share_with_user_and_group_recipients(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    direct = make_user(email="direct@test.local", role=UserRole.client)
    member = make_user(email="member@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="grp", description=None, is_company_inbox=False
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=member)
    db.commit()

    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[direct.id],
        recipient_group_ids=[g.id],
        expires_at=_future(),
    )
    db.commit()

    assert share_svc.is_authorized_to_download(db, user=direct, share=share) is True
    assert share_svc.is_authorized_to_download(db, user=member, share=share) is True
