"""Enrolled TOTP is now challenged after OIDC and after a passkey.

Both paths called `finalize_successful_login` directly, so a user who had
switched 2FA on was fully authenticated by ONE factor whenever they signed in
that way - silently, which is the worst version of this bug: the account page
said two-factor was on.

The audit found the OIDC half. The passkey half is the same defect one route
over, and worse in one respect: `/webauthn/begin` asks for
UserVerificationRequirement.PREFERRED and verification runs with
require_user_verification=False, so the ceremony may have been a single
possession factor.

`twofa_policy.is_2fa_required` is NOT the predicate - it returns False once the
user HAS TOTP, because it answers "must they still set it up". `totp_svc.is_enabled`
is the question, exactly as the password flow asks it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.user import UserRole
from app.models.user_totp import UserTOTP
from app.services import oidc as oidc_svc

from ._oidc_helpers import install_jwks_mock, make_claims, patch_exchange


def _enable_totp(db, user_id: int) -> None:
    db.add(
        UserTOTP(
            user_id=user_id,
            secret_encrypted=b"dummy",
            enabled_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            last_used_counter=0,
        )
    )
    db.commit()


async def _oidc_callback(client, monkeypatch, provider, *, sub, email):
    state, nonce = "s" * 24, "n" * 24
    install_jwks_mock(monkeypatch)
    patch_exchange(monkeypatch, make_claims(provider, sub=sub, email=email, nonce=nonce))
    oidc_svc.reset_discovery_cache()
    return await client.get(
        f"/api/auth/oidc/callback/{provider.id}",
        params={"code": "abc", "state": state},
        cookies={oidc_svc.STATE_COOKIE: f"{state}::{provider.id}::{nonce}"},
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_sso_without_2fa_still_signs_straight_in(
    db, client, monkeypatch, make_provider, make_user
):
    """The interstitial must not appear for the users who never enrolled."""
    p = make_provider()
    u = make_user(email="plain@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-plain"
    db.commit()

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-plain", email="plain@example.com")

    assert r.status_code == 302
    assert "/login/2fa" not in r.headers["location"]
    assert "fh_refresh" in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_sso_with_totp_enabled_gets_no_session_yet(
    db, client, monkeypatch, make_provider, make_user
):
    """THE defect: this used to hand over a full session."""
    p = make_provider()
    u = make_user(email="2fa@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-2fa"
    db.commit()
    _enable_totp(db, u.id)

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-2fa", email="2fa@example.com")

    assert r.status_code == 302
    assert "/login/2fa?pending=" in r.headers["location"]
    # The half-authenticated redirect must not carry a real session with it.
    assert "fh_refresh" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_the_pending_token_is_not_an_access_token(
    db, client, monkeypatch, make_provider, make_user
):
    """It grants nothing on its own. resolve_user_from_access_token rejects any
    type that is not "access", so this fails closed everywhere a real token is
    expected - which is what makes the whole design additive."""
    p = make_provider()
    u = make_user(email="pend@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-pend"
    db.commit()
    _enable_totp(db, u.id)

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-pend", email="pend@example.com")
    pending = r.headers["location"].split("pending=", 1)[1]

    me = await client.get("/api/account/me", headers={"Authorization": f"Bearer {pending}"})
    assert me.status_code == 401, me.text


@pytest.mark.asyncio
async def test_a_correct_code_completes_the_login(
    db, client, monkeypatch, make_provider, make_user
):
    p = make_provider()
    u = make_user(email="ok@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-ok"
    db.commit()
    _enable_totp(db, u.id)

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-ok", email="ok@example.com")
    pending = r.headers["location"].split("pending=", 1)[1]

    from app.services import totp as totp_svc

    monkeypatch.setattr(totp_svc, "verify_at_login", lambda db, *, user, code: True)
    done = await client.post(
        "/api/auth/2fa/complete", json={"pending_token": pending, "totp_code": "123456"}
    )

    assert done.status_code == 200, done.text
    assert done.json()["access_token"]
    assert "fh_refresh" in done.headers.get("set-cookie", "")


async def _always_matches(db, *, user, code, request=None) -> bool:
    return True


@pytest.mark.asyncio
async def test_a_recovery_code_also_completes_the_login(
    db, client, monkeypatch, make_provider, make_user
):
    """Without this an SSO user who loses their authenticator has no route back
    into their own account short of an operator on the host."""
    p = make_provider()
    u = make_user(email="rec@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-rec"
    db.commit()
    _enable_totp(db, u.id)

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-rec", email="rec@example.com")
    pending = r.headers["location"].split("pending=", 1)[1]

    from app.services import totp as totp_svc

    monkeypatch.setattr(
        # The async variant: the recovery-code Argon2 loop runs off the event
        # loop now, so the route calls `aconsume_recovery_code`.
        totp_svc, "aconsume_recovery_code", _always_matches
    )
    done = await client.post(
        "/api/auth/2fa/complete",
        json={"pending_token": pending, "recovery_code": "abcd-efgh"},
    )

    assert done.status_code == 200, done.text


@pytest.mark.asyncio
async def test_a_wrong_code_is_refused_and_counted(
    db, client, monkeypatch, make_provider, make_user
):
    """And the lockout counter must still be armed. record_success clears
    failed_login_count and locked_until, so calling it at the FIRST factor - as
    the OIDC callback used to - would hand a failing second factor a freshly
    reset counter."""
    from app.models.user import User

    p = make_provider()
    u = make_user(email="bad@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-bad"
    u.failed_login_count = 3
    db.commit()
    _enable_totp(db, u.id)

    r = await _oidc_callback(client, monkeypatch, p, sub="sub-bad", email="bad@example.com")
    pending = r.headers["location"].split("pending=", 1)[1]

    db.expire_all()
    assert db.query(User).filter(User.id == u.id).one().failed_login_count == 3, (
        "the first factor must not reset the lockout counter"
    )

    from app.services import totp as totp_svc

    monkeypatch.setattr(totp_svc, "verify_at_login", lambda db, *, user, code: False)
    done = await client.post(
        "/api/auth/2fa/complete", json={"pending_token": pending, "totp_code": "000000"}
    )

    assert done.status_code == 401
    assert done.json()["code"] == "INVALID_TOTP"
    db.expire_all()
    assert db.query(User).filter(User.id == u.id).one().failed_login_count == 4


@pytest.mark.asyncio
async def test_a_passkey_login_also_challenges_totp(db, client, monkeypatch, make_user):
    """The same gap one route over. A passkey is not automatically two factors:
    /begin asks for UserVerificationRequirement.PREFERRED and verification runs
    with require_user_verification=False."""
    from app.services import webauthn as webauthn_svc

    u = make_user(email="pk@example.com", role=UserRole.client, password="Pass12345678!")
    db.commit()
    _enable_totp(db, u.id)

    async def _fake_complete(db_, *, session_key, credential_response):
        return u

    monkeypatch.setattr(webauthn_svc, "authenticate_complete", _fake_complete)

    r = await client.post(
        "/api/auth/webauthn/complete",
        json={"session": "sess", "credential": {"id": "x"}},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("pending_2fa_token"), body
    assert "access_token" not in body
    assert "fh_refresh" not in r.headers.get("set-cookie", "")
