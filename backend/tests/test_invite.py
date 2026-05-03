"""Invite token lifecycle: create / consume / expiry / single-use."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import invite as invite_svc


@pytest.mark.asyncio
async def test_create_returns_record_and_plaintext(make_user, db):
    inviter = make_user(role=UserRole.admin)
    record, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    db.commit()

    assert plaintext
    assert plaintext != record.token_hash  # plaintext is not the stored hash
    assert record.email.endswith("@test.local")
    assert record.expires_at > datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_consume_unknown_token_raises(db):
    with pytest.raises(AppError) as exc:
        invite_svc.consume_invite(db, plaintext_token="not_a_real_token_at_all")
    assert exc.value.status_code == 404
    assert exc.value.code == "INVITE_INVALID"


@pytest.mark.asyncio
async def test_consume_expired_token_raises(make_user, db):
    inviter = make_user(role=UserRole.admin)
    record, plaintext = invite_svc.create_invite(
        db,
        email="newbie@test.local",
        target_role=UserRole.client,
        created_by=inviter,
        ttl=timedelta(seconds=1),
    )
    record.expires_at = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
    db.commit()

    with pytest.raises(AppError) as exc:
        invite_svc.consume_invite(db, plaintext_token=plaintext)
    assert exc.value.code == "INVITE_EXPIRED"


@pytest.mark.asyncio
async def test_consume_already_used_token_raises(make_user, db):
    inviter = make_user(role=UserRole.admin)
    record, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    record.used_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    db.commit()

    with pytest.raises(AppError) as exc:
        invite_svc.consume_invite(db, plaintext_token=plaintext)
    assert exc.value.code == "INVITE_USED"
