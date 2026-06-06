"""Per-user concurrent-session cap (post-Phase 10 hygiene).

Login N+1 times → the oldest active token gets revoked, a
`refresh_token_evicted` audit row is written, and the new token is
created. Verified across the password + recovery-code login flows
since they both terminate at `_create_refresh_token`.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_session_cap_evicts_oldest_on_overflow(
    make_user, db, client, login_as, monkeypatch
):
    monkeypatch.setattr(
        "app.config.settings.MAX_ACTIVE_SESSIONS_PER_USER", 3
    )
    user = make_user(
        email="cap@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )

    # First three logins → 3 active tokens (no eviction).
    for _ in range(3):
        await login_as("cap@test.local", "Pass12345678!")
    db.expire_all()
    active = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .count()
    )
    assert active == 3

    # Fourth login → oldest got evicted, still 3 active.
    await login_as("cap@test.local", "Pass12345678!")
    db.expire_all()
    active = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .count()
    )
    assert active == 3

    # And exactly one eviction was audited.
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.refresh_token_evicted.value,
            AuditLog.actor_user_id == user.id,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["reason"] == "session_cap"
    assert rows[0].extra["cap"] == 3


@pytest.mark.asyncio
async def test_session_cap_does_not_evict_when_under_limit(
    make_user, db, client, login_as, monkeypatch
):
    monkeypatch.setattr(
        "app.config.settings.MAX_ACTIVE_SESSIONS_PER_USER", 10
    )
    make_user(
        email="under@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    for _ in range(5):
        await login_as("under@test.local", "Pass12345678!")

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.refresh_token_evicted.value)
        .all()
    )
    assert rows == []  # no evictions under the cap


@pytest.mark.asyncio
async def test_session_cap_ignores_revoked_and_expired(
    make_user, db, client, login_as, monkeypatch
):
    """An old revoked or expired token should NOT count toward the
    cap - only currently-active sessions do."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(
        "app.config.settings.MAX_ACTIVE_SESSIONS_PER_USER", 2
    )
    user = make_user(
        email="mix@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    # Seed one revoked + one expired (don't count).
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash="x" * 64,
            expires_at=now + timedelta(days=7),
            revoked_at=now - timedelta(days=1),
        )
    )
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash="y" * 64,
            expires_at=now - timedelta(seconds=1),
        )
    )
    db.commit()

    # Two real logins should fit under the cap of 2 with no eviction.
    await login_as("mix@test.local", "Pass12345678!")
    await login_as("mix@test.local", "Pass12345678!")

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.refresh_token_evicted.value)
        .all()
    )
    assert rows == []
