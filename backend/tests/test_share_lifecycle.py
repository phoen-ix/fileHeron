"""Share creation, listing, authorization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
from app.models.share import ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import share as share_svc


def _future(hours: int = 1) -> datetime:
    return (datetime.now(tz=timezone.utc) + timedelta(hours=hours)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_create_share_writes_recipient_row(make_user, db):
    # Use admin senders so we don't have to wire up client_employee_connections
    # in tests that aren't about connection authorization.
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()

    rs = db.query(ShareRecipient).filter(ShareRecipient.share_id == share.id).all()
    assert len(rs) == 1
    assert rs[0].recipient_user_id == recipient.id
    assert share.state == ShareState.active


@pytest.mark.asyncio
async def test_create_share_rejects_self(make_user, db):
    me = make_user(email="me@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        share_svc.create_share(
            db,
            created_by=me,
            kind=ShareKind.outbound,
            recipient_user_ids=[me.id],
            expires_at=_future(),
        )
    assert exc.value.code == "SELF_SHARE"


@pytest.mark.asyncio
async def test_create_share_accepts_tz_aware_expiry(make_user, db):
    """Frontend sends expires_at as ISO with Z (dayjs.toISOString),
    so Pydantic produces a tz-aware datetime. The service must
    accept it — comparing tz-aware to naive _utcnow() used to crash
    with TypeError."""
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    aware_future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    assert aware_future.tzinfo is not None  # sanity check the test premise

    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=aware_future,
    )
    db.commit()

    # Stored value should be naive (codebase convention).
    assert share.expires_at.tzinfo is None
    assert share.state == ShareState.active


@pytest.mark.asyncio
async def test_create_share_rejects_past_expiry(make_user, db):
    # Use admin senders so we don't have to wire up client_employee_connections
    # in tests that aren't about connection authorization.
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(AppError) as exc:
        share_svc.create_share(
            db,
            created_by=sender,
            kind=ShareKind.outbound,
            recipient_user_ids=[recipient.id],
            expires_at=past,
        )
    assert exc.value.code == "EXPIRY_IN_PAST"


@pytest.mark.asyncio
async def test_authorization_matrix(make_user, db):
    # Use admin senders so we don't have to wire up client_employee_connections
    # in tests that aren't about connection authorization.
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    bystander = make_user(email="other@test.local")
    admin = make_user(email="admin@test.local", role=UserRole.admin)

    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()

    assert share_svc.is_authorized_to_download(db, user=sender, share=share) is True
    assert share_svc.is_authorized_to_download(db, user=recipient, share=share) is True
    assert share_svc.is_authorized_to_download(db, user=admin, share=share) is True
    assert share_svc.is_authorized_to_download(db, user=bystander, share=share) is False


@pytest.mark.asyncio
async def test_outbox_inbox_listing(make_user, db):
    # Use admin senders so we don't have to wire up client_employee_connections
    # in tests that aren't about connection authorization.
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    bystander = make_user(email="other@test.local")

    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()

    assert share_svc.list_shares_for_user(db, user=sender, box="outbox")[1] == 1
    assert share_svc.list_shares_for_user(db, user=sender, box="inbox")[1] == 0
    assert share_svc.list_shares_for_user(db, user=recipient, box="inbox")[1] == 1
    assert share_svc.list_shares_for_user(db, user=recipient, box="outbox")[1] == 0
    assert share_svc.list_shares_for_user(db, user=bystander, box="inbox")[1] == 0


@pytest.mark.asyncio
async def test_revoke_only_by_sender_or_admin(make_user, db):
    # Use admin senders so we don't have to wire up client_employee_connections
    # in tests that aren't about connection authorization.
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="customer@test.local")
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()

    with pytest.raises(AppError) as exc:
        share_svc.revoke_share(db, user=recipient, share=share)
    assert exc.value.code == "FORBIDDEN"

    share_svc.revoke_share(db, user=sender, share=share)
    db.commit()
    db.expire_all()
    assert share.state == ShareState.revoked
