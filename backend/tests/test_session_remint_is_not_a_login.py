"""Sign-out-others and change-password re-mint the caller's session. They are
not sign-ins, and must not be recorded as ones.

Both routes used to go through `auth.finalize_successful_login`, which writes
the login forensics - a `login_attempts` success row, a `login_success` audit
event and a known-device upsert - so the admin Sign-in page listed a password
change as a sign-in, and the anomaly job's "this source also logged in
successfully" exemption counted it (audit 2026-11-07). `jwt_session.remint_session`
mints the tokens and nothing else.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.known_device import KnownDevice
from app.models.login_attempt import LoginAttempt
from app.models.user import UserRole


def _forensics(db) -> tuple[int, int, int]:
    attempts = db.query(LoginAttempt).count()
    successes = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.login_success.value)
        .count()
    )
    return attempts, successes, db.query(KnownDevice).count()


@pytest.mark.asyncio
async def test_revoke_others_writes_no_login_forensics(make_user, db, client, login_as):
    make_user(email="remint@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("remint@test.local", "Pass12345678!")
    db.expire_all()
    before = _forensics(db)
    assert before[1] == 1, "the real sign-in is the one login_success"

    r = await client.post(
        "/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert "fh_refresh" in r.cookies, "the device is re-minted"

    db.expire_all()
    assert _forensics(db) == before, "but nothing calls it a sign-in"


@pytest.mark.asyncio
async def test_change_password_writes_no_login_forensics(make_user, db, client, login_as):
    make_user(email="remint2@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("remint2@test.local", "Pass12345678!")
    db.expire_all()
    before = _forensics(db)

    r = await client.post(
        "/api/account/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Pass12345678!", "new_password": "Another-Long-Secret-42"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"], "the device is re-minted"
    assert "fh_refresh" in r.cookies

    db.expire_all()
    assert _forensics(db) == before
    # The action's own trail is untouched.
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.password_changed.value)
        .count()
        == 1
    )
