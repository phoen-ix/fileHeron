"""An issuer that ends in "/" could never complete a login.

`_verify_token_response` passed `issuer=provider.issuer_url.rstrip("/")` to
pyjwt, which compares the `iss` claim byte-for-byte - while the discovery check
rstrips BOTH sides. So a provider whose canonical issuer ends in a slash passed
discovery, exchanged its code, and then died at the last step with
OIDC_BAD_ISSUER. The shipped Authentik preset template ends in a slash
(`https://{host}/application/o/{slug}/`), so that preset could never log anyone
in, and `POST /api/admin/oidc/{id}/test-connection` reported "ok" for it because
it rstrips too.

This was untestable by construction until 2026-08-15: `_oidc_helpers.make_claims`
built `iss` with the same `.rstrip("/")` expression the implementation applied to
its expectation, so the fixture could never disagree with the code.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import oidc as oidc_svc

from ._oidc_helpers import make_claims, patch_exchange

SLASHED = "https://auth.example.com/application/o/fileheron/"


@pytest.mark.asyncio
async def test_a_slash_terminated_issuer_can_log_in(make_provider, db, monkeypatch):
    """The Authentik shape. The IdP echoes its issuer verbatim, slash included."""
    p = make_provider(issuer_url=SLASHED)
    claims = make_claims(p, sub="sub-1", email="user@example.com")
    assert claims["iss"] == SLASHED, "fixture must echo the issuer verbatim"
    patch_exchange(monkeypatch, claims)
    oidc_svc.reset_discovery_cache()

    verified = await oidc_svc._verify_token_response(
        p, {"id_token": _token(monkeypatch, claims)}, expected_nonce=None
    )
    assert verified["sub"] == "sub-1"


@pytest.mark.asyncio
async def test_the_stored_form_may_carry_the_slash_or_not(make_provider, db, monkeypatch):
    """Normalising only at storage would fix the preset but not a hand-typed
    custom issuer, so both directions have to work."""
    p = make_provider(issuer_url=SLASHED.rstrip("/"))
    claims = make_claims(p, sub="sub-2", email="user2@example.com")
    claims["iss"] = SLASHED  # IdP still sends the slash
    patch_exchange(monkeypatch, claims)
    oidc_svc.reset_discovery_cache()

    verified = await oidc_svc._verify_token_response(
        p, {"id_token": _token(monkeypatch, claims)}, expected_nonce=None
    )
    assert verified["sub"] == "sub-2"


@pytest.mark.asyncio
async def test_a_genuinely_different_issuer_is_still_refused(make_provider, db, monkeypatch):
    """Tolerating a trailing slash must not tolerate anything else."""
    p = make_provider(issuer_url=SLASHED)
    claims = make_claims(p, sub="sub-3", email="user3@example.com")
    claims["iss"] = "https://auth.evil.example/application/o/fileheron/"
    patch_exchange(monkeypatch, claims)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc._verify_token_response(
        p, {"id_token": _token(monkeypatch, claims)}, expected_nonce=None
    )
    assert exc.value.code == "OIDC_BAD_ISSUER"


@pytest.mark.asyncio
async def test_a_token_with_no_issuer_at_all_is_refused(make_provider, db, monkeypatch):
    """Dropping pyjwt's `issuer=` kwarg means we own the check - including the
    presence check, since `iss` was never in the `require` list."""
    p = make_provider(issuer_url=SLASHED)
    claims = make_claims(p, sub="sub-4", email="user4@example.com")
    del claims["iss"]
    patch_exchange(monkeypatch, claims)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc._verify_token_response(
        p, {"id_token": _token(monkeypatch, claims)}, expected_nonce=None
    )
    assert exc.value.code == "OIDC_BAD_ISSUER"


def _token(monkeypatch, claims):
    from ._oidc_helpers import install_jwks_mock, sign_id_token

    install_jwks_mock(monkeypatch)
    return sign_id_token(claims)
