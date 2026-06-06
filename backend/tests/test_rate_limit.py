"""Per-account lockout tests. Per-IP rate limit is Redis-backed and tested
separately (Phase 1b smoke run); these tests exercise the DB-backed lockout
that triggers after N consecutive bad-password attempts.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.models.user import User


@pytest.mark.asyncio
async def test_lockout_after_five_failed_passwords(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")

    for i in range(5):
        r = await client.post(
            "/api/auth/login", json={"email": "alice@test.local", "password": "WRONG"}
        )
        assert r.status_code == 401, f"attempt {i+1}: {r.text}"
        assert r.json()["code"] == "INVALID_CREDENTIALS"

    # 6th attempt - even with the CORRECT password - is blocked by the lockout.
    locked = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    assert locked.status_code == 423
    body = locked.json()
    assert body["code"] == "ACCOUNT_LOCKED"
    assert "locked_until" in body.get("details", {})

    db.expire_all()
    user = db.query(User).filter(User.email.like("a%test.local")).one()
    assert user.failed_login_count == 5
    assert user.locked_until is not None
    assert user.lockout_email_sent_at is not None  # warning email was attempted

    # login_attempts table records each one.
    attempts = db.query(LoginAttempt).order_by(LoginAttempt.id).all()
    outcomes = [a.outcome for a in attempts]
    assert outcomes.count(LoginOutcome.bad_password.value) == 5
    assert outcomes.count(LoginOutcome.locked.value) == 1


@pytest.mark.asyncio
async def test_successful_login_resets_failure_counter(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")

    # Three failures.
    for _ in range(3):
        await client.post(
            "/api/auth/login", json={"email": "alice@test.local", "password": "WRONG"}
        )

    db.expire_all()
    user = db.query(User).filter(User.email.like("a%test.local")).one()
    assert user.failed_login_count == 3

    # Now a correct login resets the counter.
    ok = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    assert ok.status_code == 200

    db.expire_all()
    user = db.query(User).filter(User.email.like("a%test.local")).one()
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_lockout_threshold_under_concurrency(make_user, db):
    """Wave 1 P1-4 regression. Six record_failure calls on a fresh user
    → lockout fires exactly once (on the 5th call), not 6 times. The
    row-level write lock via `db.refresh(user, with_for_update=True)`
    serializes the read-modify-write across concurrent callers in
    MariaDB; subsequent calls past the threshold see is_account_locked
    and don't re-fire just_locked.

    SQLite ignores FOR UPDATE and is single-threaded in pytest, so this
    test exercises the invariant (one lockout transition per
    threshold-crossing) rather than true parallel contention. The
    pre-fix Python-layer counter+threshold check would have read
    failed_login_count=0 in all 6 calls if they actually interleaved,
    incrementing only to 1 instead of 6 and never crossing the
    threshold.
    """
    from app.services import rate_limit

    user = make_user(email="alice@test.local")

    results = []
    for _ in range(6):
        just_locked, _ = rate_limit.record_failure(db, user=user)
        results.append(just_locked)
    db.commit()
    db.refresh(user)

    assert results.count(True) == 1, f"exactly one lockout transition expected; got {results}"
    assert results[4] is True, "lockout should fire on the 5th failure"
    assert user.failed_login_count == 6, "all 6 increments must persist"
    assert user.locked_until is not None


@pytest.mark.asyncio
async def test_account_locked_audit_emitted_once(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")

    # Trigger lockout.
    for _ in range(5):
        await client.post(
            "/api/auth/login", json={"email": "alice@test.local", "password": "WRONG"}
        )

    db.expire_all()
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.account_locked.value)
        .all()
    )
    # account_locked audit fires when the lockout transitions, plus on each
    # subsequent attempt against the locked account. For the 5-failure path we
    # expect exactly one (the transition).
    assert len(rows) == 1
