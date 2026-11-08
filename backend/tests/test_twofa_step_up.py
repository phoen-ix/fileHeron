"""Disabling TOTP, regenerating recovery codes and removing a passkey are
step-up actions, and the step-up throttle covers BOTH secrets of the 2FA gates.

Before 2026-11-08 `/api/account/2fa/disable` and `/2fa/recovery-codes/regenerate`
verified the password with a bare `argon2_verify` and the code with no throttle,
no counter and no record: a session that knew the password could brute-force
the six-digit code (three valid codes per 30 s window) until the second factor
was gone, nothing logged the attempts, and the failures answered 401 - the
status the SPA's refresh interceptor reads as "session expired". The
change-password route two screens up was throttled; these were the exception.
Removing a passkey needed only a live session while adding one cost the
password - and with passkey sign-in on, the passkey can be the account's only
second factor.

Now all three go through `services/step_up.py`. The throttle tests put the real
counter back (conftest neutralises it) and prove the one thing a refactor would
most plausibly lose: that a CORRECT password does not reset the window before
the code is checked, which would hand a hijacked session an unthrottled oracle
for the code.
"""
from __future__ import annotations

import pyotp
import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import User, UserRole

# Captured at import time, before conftest's autouse fixture neutralises them.
from app.services.rate_limit import check_user_allowed as _real_check_user_allowed
from app.services.rate_limit import reset_user_window as _real_reset_user_window

PW = "Pass12345678!"
WRONG = "not-the-password"
WRONG_CODE = "000000"
ROUTES = ["/api/account/2fa/disable", "/api/account/2fa/recovery-codes/regenerate"]


class _FakeRedis:
    """Just the three calls the throttle makes - the suite can reach the LIVE
    Redis, so a test that exercises a counter must never depend on it."""

    def __init__(self):
        self.store: dict[str, int] = {}

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, _sec):
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


async def _enrolled(make_user, client, login_as, email: str):
    """A signed-in user with TOTP on: (headers, secret, recovery codes). The
    2FA routes live on the ungated setup router, so the token minted before
    enrolment stays good for them."""
    make_user(email=email, role=UserRole.client, password=PW)
    token, _ = await login_as(email, PW)
    headers = {"Authorization": f"Bearer {token}"}
    setup = await client.post("/api/account/2fa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret_b32"]
    enable = await client.post(
        "/api/account/2fa/enable", headers=headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert enable.status_code == 200, enable.text
    return headers, secret, enable.json()["recovery_codes"]


def _step_up_failures(db) -> list[str]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.step_up_failed.value)
        .order_by(AuditLog.id)
        .all()
    )
    return [row.extra["reason"] for row in rows]


async def _still_enabled(client, headers) -> bool:
    r = await client.get("/api/account/2fa/status", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["enabled"] and r.json()["recovery_codes_remaining"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_wrong_password_answers_403_and_is_audited(
    make_user, client, login_as, db, route
):
    headers, _secret, codes = await _enrolled(make_user, client, login_as, "a@test.local")

    r = await client.post(route, headers=headers, json={"password": WRONG, "code_or_recovery": codes[0]})

    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INVALID_PASSWORD"
    assert _step_up_failures(db) == ["bad_password"]
    assert await _still_enabled(client, headers), "a refused attempt must change nothing"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_a_wrong_code_answers_403_and_is_audited(make_user, client, login_as, db, route):
    """The code is the second secret of the same gate: same status, same row.
    401 here would make the SPA refresh and replay the same wrong code."""
    headers, _secret, _codes = await _enrolled(make_user, client, login_as, "a@test.local")

    r = await client.post(route, headers=headers, json={"password": PW, "code_or_recovery": WRONG_CODE})

    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INVALID_TOTP"
    assert _step_up_failures(db) == ["bad_second_factor"]
    assert await _still_enabled(client, headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_code_guesses_are_throttled_even_with_the_right_password(
    make_user, client, login_as, db, route, real_step_up_throttle
):
    """The attack this gate exists for: the password is known, the code is
    guessed. A correct password must NOT clear the window on its way to the
    code check, or the limit never fills."""
    from app.services import settings_registry

    headers, _secret, _codes = await _enrolled(make_user, client, login_as, "t@test.local")
    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))

    statuses = []
    for _ in range(limit + 1):
        r = await client.post(route, headers=headers, json={"password": PW, "code_or_recovery": WRONG_CODE})
        statuses.append(r.status_code)

    assert statuses[0] == 403, statuses
    assert statuses[-1] == 429, statuses
    assert _step_up_failures(db)[-1] == "rate_limited"
    # And it is the step-up counter, never the login lockout: a hijacked session
    # must not be able to lock the real owner out of the login page.
    user = db.query(User).filter(User.email == "t@test.local").one()
    db.refresh(user)
    assert user.locked_until is None
    assert await _still_enabled(client, headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_passing_both_secrets_clears_the_window(
    make_user, client, login_as, db, route, real_step_up_throttle
):
    """Ordinary use never accumulates: one typo, then the right code, and the
    counter is gone - cleared AFTER the second factor, not between the two."""
    from app.services import rate_limit as rl

    headers, _secret, codes = await _enrolled(make_user, client, login_as, "c@test.local")
    user_id = db.query(User.id).filter(User.email == "c@test.local").scalar()
    key = rl._user_key("step_up", user_id)

    bad = await client.post(route, headers=headers, json={"password": PW, "code_or_recovery": WRONG_CODE})
    assert bad.status_code == 403
    assert real_step_up_throttle.store.get(key) == 1, "the failed attempt was spent"

    good = await client.post(route, headers=headers, json={"password": PW, "code_or_recovery": codes[0]})
    assert good.status_code == 200, good.text
    assert key not in real_step_up_throttle.store, "a passed gate forgets the attempts"


# --- passkeys ---------------------------------------------------------------


def _passkey(db, user):
    from app.models.user_webauthn_credential import UserWebAuthnCredential

    cred = UserWebAuthnCredential(
        user_id=user.id,
        credential_id=b"cred-a",
        public_key=b"pk-a",
        sign_count=0,
        transports="internal",
        name="Phone",
        discoverable=True,
    )
    db.add(cred)
    db.commit()
    return cred


@pytest.mark.asyncio
async def test_removing_a_passkey_needs_the_password(make_user, client, login_as, db):
    from app.models.user_webauthn_credential import UserWebAuthnCredential

    u = make_user(email="p@test.local", role=UserRole.client, password=PW)
    cred = _passkey(db, u)
    token, _ = await login_as("p@test.local", PW)
    headers = {"Authorization": f"Bearer {token}"}

    no_body = await client.delete(f"/api/account/webauthn/{cred.id}", headers=headers)
    assert no_body.status_code == 422, no_body.text

    wrong = await client.request(
        "DELETE", f"/api/account/webauthn/{cred.id}", json={"password": WRONG}, headers=headers
    )
    assert wrong.status_code == 403, wrong.text
    assert wrong.json()["code"] == "INVALID_PASSWORD"
    assert _step_up_failures(db) == ["bad_password"]
    assert db.query(UserWebAuthnCredential).filter_by(id=cred.id).count() == 1

    right = await client.request(
        "DELETE", f"/api/account/webauthn/{cred.id}", json={"password": PW}, headers=headers
    )
    assert right.status_code == 204, right.text
    assert db.query(UserWebAuthnCredential).filter_by(id=cred.id).count() == 0
