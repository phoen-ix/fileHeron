"""An expired lockout must start a fresh failure count.

Only a successful login reset `failed_login_count`, so once an account had been
locked the counter stayed at the threshold. After the 15-minute lockout expired,
the very next wrong password hit `>= threshold` again and re-locked immediately -
turning a temporary lockout into a permanent one for anyone who mistypes, and
letting a third party hold an account locked forever at one attempt per window
(audit 2026-07-30).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.user import UserRole
from app.services import rate_limit as rl
from app.utils.timeutil import utc_now


@pytest.fixture
def user(make_user):
    return make_user(email="victim@test.local", role=UserRole.employee)


def _threshold(db) -> int:
    from app.services import settings_registry
    return settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD)


def test_expired_lockout_resets_the_counter(db, user):
    n = _threshold(db)
    for _ in range(n):
        rl.record_failure(db, user=user)
    db.commit()
    assert user.locked_until is not None, "should be locked after the threshold"

    # Serve the lockout.
    user.locked_until = utc_now() - timedelta(seconds=1)
    db.commit()

    # ONE more wrong password must not immediately re-lock.
    rl.record_failure(db, user=user)
    db.commit()
    assert user.failed_login_count == 1
    assert not rl.is_account_locked(user), (
        "a single failure after an expired lockout re-locked the account"
    )


def test_threshold_still_locks(db, user):
    """Control: the lockout itself must still work."""
    for _ in range(_threshold(db)):
        rl.record_failure(db, user=user)
    db.commit()
    assert rl.is_account_locked(user)


def test_failures_during_an_active_lockout_do_not_reset(db, user):
    """Only an EXPIRED lockout resets. While still locked, the count keeps
    climbing - otherwise the reset would itself be a way to dodge the lock."""
    n = _threshold(db)
    for _ in range(n):
        rl.record_failure(db, user=user)
    db.commit()
    assert rl.is_account_locked(user)

    rl.record_failure(db, user=user)
    db.commit()
    assert user.failed_login_count == n + 1
    assert rl.is_account_locked(user)
