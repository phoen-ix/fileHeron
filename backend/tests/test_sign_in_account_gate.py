"""`auth.assert_account_may_sign_in` is the ONE account-state gate a sign-in
passes after its credential verified.

Three flows carried their own copy - the password flow, the passkey-only flow
and the password+passkey completion route - and they had drifted: the route
refused a disabled, locked or unverified account without writing the
`login_attempts` row the other two wrote (audit 2026-11-07). The pending-token
completions after an SSO or passkey first factor deliberately keep a
locked-only check (see the gate's docstring), so they are not listed here.
"""
from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.models.user import UserRole
from app.routers import webauthn as webauthn_router
from app.services import auth as auth_svc
from app.services import webauthn as webauthn_svc
from app.utils.timeutil import utc_now


def _attempts(db, outcome: LoginOutcome) -> int:
    return db.query(LoginAttempt).filter(LoginAttempt.outcome == outcome.value).count()


def test_the_three_sign_in_flows_use_the_shared_gate():
    for fn in (
        auth_svc.authenticate_first_factor,
        auth_svc.login_with_passkey,
        webauthn_router.auth_complete,
    ):
        assert "assert_account_may_sign_in(" in inspect.getsource(fn), fn.__name__


@pytest.mark.parametrize(
    ("state", "code", "status", "outcome"),
    [
        ("disabled", "ACCOUNT_DISABLED", 403, LoginOutcome.account_disabled),
        ("locked", "ACCOUNT_LOCKED", 423, LoginOutcome.locked),
        ("unverified", "EMAIL_NOT_VERIFIED", 403, LoginOutcome.email_not_verified),
    ],
)
def test_each_refusal_writes_its_login_attempts_row(make_user, db, state, code, status, outcome):
    user = make_user(
        email=f"{state}@test.local",
        is_disabled=(state == "disabled"),
        email_verified=(state != "unverified"),
    )
    if state == "locked":
        user.locked_until = utc_now() + timedelta(minutes=10)
        db.commit()

    with pytest.raises(AppError) as exc:
        auth_svc.assert_account_may_sign_in(db, user, request=None)
    assert exc.value.code == code
    assert exc.value.status_code == status
    if state == "locked":
        assert exc.value.details["locked_until"] == user.locked_until.isoformat()

    db.expire_all()
    assert _attempts(db, outcome) == 1


def test_a_healthy_account_passes_and_writes_nothing(make_user, db):
    user = make_user(email="fine@test.local")
    auth_svc.assert_account_may_sign_in(db, user, request=None)
    assert db.query(LoginAttempt).count() == 0


@pytest.mark.asyncio
async def test_the_passkey_completion_route_now_records_a_refused_disabled_account(
    db, client, monkeypatch, make_user
):
    """The copy that had drifted: /api/auth/webauthn/complete refused the
    account but left no trace of the attempt."""
    u = make_user(email="pkdis@test.local", role=UserRole.client, password="Pass12345678!")
    u.is_disabled = True
    db.commit()

    async def _fake_complete(db_, *, session_key, credential_response):
        return webauthn_svc.PasskeyAuthResult(user=u, user_verified=True)

    monkeypatch.setattr(webauthn_svc, "authenticate_complete", _fake_complete)

    r = await client.post(
        "/api/auth/webauthn/complete",
        json={"session": "s", "credential": {"id": "x", "rawId": "x", "response": {}, "type": "public-key"}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "ACCOUNT_DISABLED"
    db.expire_all()
    assert _attempts(db, LoginOutcome.account_disabled) == 1
