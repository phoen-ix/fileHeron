"""Changing your password or email spends the per-USER step-up window
(audit 2026-11-19).

Both forms verify the current password, and both were throttled per IP only
and recorded nowhere: a hijacked session on rotating egress had an unlogged
oracle, and change-password's success is a takeover (a new password, every
other session revoked). They now spend the same window as every step-up gate
and write the same `step_up_failed` row - while keeping their 401
`INVALID_CREDENTIALS`, which both clients already handle.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services.rate_limit import check_user_allowed as _real_check_user_allowed
from app.services.rate_limit import reset_user_window as _real_reset_user_window

PW = "Pass12345678!"
WRONG = "not-the-password-1"


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, _sec, nx=False):
        return key in self.store

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def real_step_up_throttle(monkeypatch):
    from app.services import rate_limit as rl

    monkeypatch.setattr(rl, "check_user_allowed", _real_check_user_allowed)
    monkeypatch.setattr(rl, "reset_user_window", _real_reset_user_window)
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    with rl._local_lock:
        rl._local_windows.clear()
    yield fake
    with rl._local_lock:
        rl._local_windows.clear()


async def _change_password(client, headers, current):
    return await client.post(
        "/api/account/change-password",
        json={"current_password": current, "new_password": "Brand-new-pass-77!"},
        headers=headers,
    )


async def _change_email(client, headers, current):
    return await client.post(
        "/api/account/email",
        json={"new_email": "moved@test.local", "current_password": current},
        headers=headers,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("submit", [_change_password, _change_email])
async def test_wrong_current_passwords_are_throttled_per_user_and_recorded(
    submit, make_user, db, client, login_as, real_step_up_throttle
):
    from app.services import settings as settings_svc
    from app.services import settings_registry

    settings_svc.set_value(db, key=settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE, value="true", actor=None)
    db.commit()
    user = make_user(email="form@test.local", role=UserRole.client, password=PW)
    access, _ = await login_as("form@test.local", PW)
    headers = {"Authorization": f"Bearer {access}"}
    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))

    codes = [(await submit(client, headers, WRONG)).status_code for _ in range(limit + 1)]
    assert codes[:limit] == [401] * limit, codes  # the clients' contract is unchanged
    assert codes[-1] == 429, codes                 # the per-user window ran out

    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.step_up_failed.value,
            AuditLog.actor_user_id == user.id,
        )
        .all()
    )
    reasons = sorted(r.extra["reason"] for r in rows)
    assert reasons.count("bad_password") == limit
    assert "rate_limited" in reasons

    # Even the RIGHT password waits out the window - the throttle is the point.
    assert (await submit(client, headers, PW)).status_code == 429


@pytest.mark.asyncio
async def test_a_correct_password_clears_the_window(
    make_user, db, client, login_as, real_step_up_throttle
):
    make_user(email="ok@test.local", role=UserRole.client, password=PW)
    access, _ = await login_as("ok@test.local", PW)
    headers = {"Authorization": f"Bearer {access}"}

    assert (await _change_password(client, headers, WRONG)).status_code == 401
    assert real_step_up_throttle.store, "the failed attempt was counted"
    resp = await _change_password(client, headers, PW)
    assert resp.status_code == 200, resp.text
    assert real_step_up_throttle.store == {}, "success must clear the window"
