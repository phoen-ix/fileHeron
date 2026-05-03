"""cleanup_expired_tokens worker — soft-revoke expired + hard-delete
revoked-old refresh_tokens. Idempotent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.refresh_token import RefreshToken
from app.models.user import UserRole
from app.workers.cleanup_expired_tokens import cleanup_expired_tokens


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_worker_soft_revokes_expired_tokens(
    make_user, db, monkeypatch
):
    user = make_user(email="a@test.local", role=UserRole.client)
    now = _now()

    # Active (not expired) — should be left alone.
    active = RefreshToken(
        user_id=user.id,
        token_hash="a" * 64,
        expires_at=now + timedelta(days=7),
    )
    # Expired but not revoked — should be soft-revoked.
    expired = RefreshToken(
        user_id=user.id,
        token_hash="b" * 64,
        expires_at=now - timedelta(seconds=1),
    )
    db.add_all([active, expired])
    db.commit()

    # Patch SessionLocal so the worker uses our test session's engine.
    from app.workers import cleanup_expired_tokens as worker_mod

    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None  # the worker will call commit; harmless here
    db.close = lambda: None

    result = await cleanup_expired_tokens(None)
    assert result["soft_revoked"] >= 1
    assert result["deleted"] == 0

    db.refresh(active)
    db.refresh(expired)
    assert active.revoked_at is None
    assert expired.revoked_at is not None


@pytest.mark.asyncio
async def test_worker_hard_deletes_old_revoked_tokens(
    make_user, db, monkeypatch
):
    from app.config import settings as cfg

    user = make_user(email="b@test.local", role=UserRole.client)
    now = _now()
    cutoff = now - timedelta(days=cfg.REFRESH_TOKEN_RETENTION_DAYS + 1)

    very_old_revoked = RefreshToken(
        user_id=user.id,
        token_hash="c" * 64,
        expires_at=now - timedelta(days=60),
        revoked_at=cutoff,
    )
    recently_revoked = RefreshToken(
        user_id=user.id,
        token_hash="d" * 64,
        expires_at=now + timedelta(days=7),
        revoked_at=now - timedelta(days=1),
    )
    db.add_all([very_old_revoked, recently_revoked])
    db.commit()
    very_old_id = very_old_revoked.id
    recent_id = recently_revoked.id

    from app.workers import cleanup_expired_tokens as worker_mod

    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    result = await cleanup_expired_tokens(None)
    assert result["deleted"] >= 1

    # Old revoked token gone; recently-revoked one stays.
    assert (
        db.query(RefreshToken).filter(RefreshToken.id == very_old_id).count() == 0
    )
    assert (
        db.query(RefreshToken).filter(RefreshToken.id == recent_id).count() == 1
    )


@pytest.mark.asyncio
async def test_worker_is_idempotent(make_user, db, monkeypatch):
    user = make_user(email="c@test.local", role=UserRole.client)
    now = _now()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash="e" * 64,
            expires_at=now - timedelta(seconds=1),
        )
    )
    db.commit()

    from app.workers import cleanup_expired_tokens as worker_mod

    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    db.commit = lambda: None
    db.close = lambda: None

    r1 = await cleanup_expired_tokens(None)
    r2 = await cleanup_expired_tokens(None)
    # Second pass finds nothing left to do.
    assert r2["soft_revoked"] == 0
    assert r1["soft_revoked"] >= 1
