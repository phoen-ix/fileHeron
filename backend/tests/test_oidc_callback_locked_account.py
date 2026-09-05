"""An SSO callback does not mint a session for a password-locked account.

The password, recovery and passkey doors all refuse `users.locked_until`;
the OIDC callback did not, so it wrote `last_login_at`, a refresh token and a
login audit row for an account every other door was turning away.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.user import User, UserRole
from app.services import oidc as oidc_svc
from app.utils.timeutil import utc_now

from ._oidc_helpers import install_jwks_mock, make_claims, patch_exchange


async def _callback(client, monkeypatch, provider, *, sub, email):
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
async def test_a_locked_linked_account_gets_no_session(db, client, monkeypatch, make_provider, make_user):
    p = make_provider()
    u = make_user(email="locked@example.com", role=UserRole.client)
    u.oidc_provider_id, u.oidc_subject = p.id, "sub-locked"
    u.locked_until = utc_now() + timedelta(minutes=10)
    db.commit()

    r = await _callback(client, monkeypatch, p, sub="sub-locked", email="locked@example.com")

    assert r.status_code == 302, r.text
    assert "oidc_error=ACCOUNT_LOCKED" in r.headers["location"]
    assert "fh_refresh" not in r.headers.get("set-cookie", "")
    db.expire_all()
    assert db.query(User).filter(User.id == u.id).one().last_login_at is None


@pytest.mark.asyncio
async def test_a_locked_account_is_not_auto_linked_either(db, client, monkeypatch, make_provider, make_user):
    p = make_provider()
    u = make_user(email="fresh@example.com", role=UserRole.client)
    u.locked_until = utc_now() + timedelta(minutes=10)
    db.commit()

    r = await _callback(client, monkeypatch, p, sub="sub-fresh", email="fresh@example.com")

    assert r.status_code == 302, r.text
    assert "oidc_error=ACCOUNT_LOCKED" in r.headers["location"]
    db.expire_all()
    after = db.query(User).filter(User.id == u.id).one()
    assert after.oidc_subject is None, "the lock must be checked before the link is written"
