"""Public link service tests: create / unlock / counter / lockout / revoke."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
from app.models.public_link import PublicLink
from app.models.public_link_attempt import (
    PublicLinkAttempt,
    PublicLinkAttemptOutcome,
)
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _make_share(db, owner, recipient_user_id: int) -> Share:
    """Plain active share, future expiry, single user recipient. Uses
    the share service so the schema invariants are exercised."""
    from app.services import share as share_svc

    return share_svc.create_share(
        db,
        created_by=owner,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient_user_id],
        expires_at=_now_naive() + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_create_link_returns_token_once(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()

    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=3,
        notify_on_download=False,
    )
    db.commit()
    assert len(created.plaintext_token) > 30
    assert created.record.downloads_remaining == 3


@pytest.mark.asyncio
async def test_create_link_refuses_double(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    public_link_svc.create_link(
        db, actor=owner, share=share, password=None, download_limit=None, notify_on_download=False
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        public_link_svc.create_link(
            db,
            actor=owner,
            share=share,
            password=None,
            download_limit=None,
            notify_on_download=False,
        )
    assert exc.value.code == "PUBLIC_LINK_EXISTS"


@pytest.mark.asyncio
async def test_password_verify_locks_after_repeated_failures(make_user, db, monkeypatch):
    # Tighten the threshold so the test runs fast.
    from app.services import public_link as svc
    monkeypatch.setattr(svc.settings, "PUBLIC_LINK_PASSWORD_RATE_LIMIT", 3)
    monkeypatch.setattr(svc.settings, "PUBLIC_LINK_PASSWORD_WINDOW_SEC", 900)

    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password="correct horse",
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    link = created.record

    for _ in range(3):
        ok = public_link_svc.verify_password(db, link=link, password="wrong", ip="1.2.3.4")
        assert ok is False
    db.commit()
    db.refresh(link)
    assert link.locked_until is not None and link.locked_until > _now_naive()

    # And the audit-style attempt rows show the expected pattern.
    outcomes = [
        a.outcome
        for a in db.query(PublicLinkAttempt)
        .filter(PublicLinkAttempt.public_link_id == link.id)
        .order_by(PublicLinkAttempt.id)
        .all()
    ]
    assert outcomes[-1] == PublicLinkAttemptOutcome.locked


@pytest.mark.asyncio
async def test_password_verify_correct_unlocks(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password="open sesame",
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    assert public_link_svc.verify_password(
        db, link=created.record, password="open sesame", ip="1.2.3.4"
    )


@pytest.mark.asyncio
async def test_decrement_counter_atomic_and_terminating(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=2,
        notify_on_download=False,
    )
    db.commit()
    link = created.record

    allowed, remaining = public_link_svc.decrement_counter(db, link=link)
    assert allowed is True
    assert remaining == 1
    db.commit()
    db.refresh(link)
    assert link.downloads_remaining == 1

    allowed, remaining = public_link_svc.decrement_counter(db, link=link)
    assert allowed is True
    assert remaining == 0
    db.commit()
    db.refresh(link)
    assert link.downloads_remaining == 0

    # Third call returns False — exhausted.
    allowed, _ = public_link_svc.decrement_counter(db, link=link)
    assert allowed is False


@pytest.mark.asyncio
async def test_unlimited_counter_never_decrements(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    for _ in range(5):
        allowed, remaining = public_link_svc.decrement_counter(db, link=created.record)
        assert allowed is True
        assert remaining is None  # unlimited links report no remaining
    db.refresh(created.record)
    assert created.record.downloads_remaining is None


@pytest.mark.asyncio
async def test_revoke_makes_link_unusable(make_user, db):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    public_link_svc.revoke(db, actor=owner, link=created.record)
    db.commit()
    with pytest.raises(AppError) as exc:
        public_link_svc.assert_link_usable(db, created.record)
    assert exc.value.code == "PUBLIC_LINK_REVOKED"


@pytest.mark.asyncio
async def test_assert_usable_blocks_after_share_revoked(make_user, db):
    from app.services import share as share_svc

    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, owner, recipient.id)
    db.commit()
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    share_svc.revoke_share(db, user=owner, share=share)
    db.commit()

    with pytest.raises(AppError) as exc:
        public_link_svc.assert_link_usable(db, created.record)
    assert exc.value.code == "SHARE_NOT_ACTIVE"
