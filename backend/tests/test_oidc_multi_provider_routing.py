"""Phase 10: with two providers configured concurrently, the start +
callback routes route through to the right discovery doc and the right
client_id, and a callback for provider A cannot resolve a user
linked to provider B."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import oidc as oidc_svc
from app.services import oidc_admin as oidc_admin_svc

from ._oidc_helpers import install_jwks_mock, make_claims, patch_exchange, sign_id_token


@pytest.mark.asyncio
async def test_two_providers_resolve_independent_users(
    make_provider, make_user, db, monkeypatch
):
    a = make_provider(name="A", issuer_url="https://a.example.com", client_id="client-a")
    b = make_provider(name="B", issuer_url="https://b.example.com", client_id="client-b")

    alice_a = make_user(email="alice@a.example.com", role=UserRole.employee)
    alice_a.oidc_provider_id = a.id
    alice_a.oidc_subject = "alice-sub"
    bob_b = make_user(email="bob@b.example.com", role=UserRole.employee)
    bob_b.oidc_provider_id = b.id
    bob_b.oidc_subject = "alice-sub"  # same subject, different provider
    db.commit()

    install_jwks_mock(monkeypatch)
    token_a = sign_id_token(make_claims(a, sub="alice-sub", email="alice@a.example.com"))
    token_b = sign_id_token(make_claims(b, sub="alice-sub", email="bob@b.example.com"))

    async def fake_exchange(provider, _code, kind="login"):
        return {"id_token": token_a if provider.id == a.id else token_b}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    out_a = await oidc_svc.handle_callback(
        db, provider=a, code="x", state_cookie="s", state_param="s",
        expected_nonce=None,
    )
    assert out_a.id == alice_a.id

    out_b = await oidc_svc.handle_callback(
        db, provider=b, code="x", state_cookie="s", state_param="s",
        expected_nonce=None,
    )
    assert out_b.id == bob_b.id


@pytest.mark.asyncio
async def test_callback_for_a_cannot_resolve_user_of_b(
    make_provider, make_user, db, monkeypatch
):
    """User is linked to provider B; an inbound callback claiming to
    come from provider A with the same `sub` must NOT resolve to that
    user."""
    a = make_provider(name="A", issuer_url="https://a.example.com", client_id="client-a")
    b = make_provider(name="B", issuer_url="https://b.example.com", client_id="client-b")

    user = make_user(email="multi@example.com", role=UserRole.employee)
    user.oidc_provider_id = b.id
    user.oidc_subject = "shared-sub"
    db.commit()

    claims = make_claims(a, sub="shared-sub", email="different@example.com")
    patch_exchange(monkeypatch, claims)

    # No matching (provider=A, sub) row, and the email is unknown — refuses.
    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=a, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_NO_ACCOUNT"


def test_list_enabled_providers_excludes_disabled(make_provider, db):
    on = make_provider(name="On", issuer_url="https://on.example.com", client_id="on")
    off = make_provider(
        name="Off", issuer_url="https://off.example.com", client_id="off",
        enabled=False,
    )
    enabled = oidc_admin_svc.list_enabled_providers(db)
    assert on in enabled
    assert off not in enabled


def test_is_any_enabled_reflects_at_least_one(make_provider, db):
    assert oidc_admin_svc.is_any_enabled(db) is False
    p = make_provider(enabled=False)
    assert oidc_admin_svc.is_any_enabled(db) is False
    p.enabled = True
    db.commit()
    assert oidc_admin_svc.is_any_enabled(db) is True
