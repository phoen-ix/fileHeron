"""ID-token signature, algorithm, expiry, and nonce verification.

These cover the new defenses wired in the JWKS hardening pass:
- Wrong-key signature → OIDC_BAD_SIGNATURE.
- Unknown kid (after refresh) → OIDC_KEY_NOT_FOUND.
- Algorithm in the deny-list (`none`, `HS256`) → OIDC_BAD_ID_TOKEN.
- Expired token → OIDC_TOKEN_EXPIRED.
- Nonce mismatch → OIDC_BAD_NONCE.
- JWKS endpoint 5xx → OIDC_JWKS_UNAVAILABLE."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.middleware.errors import AppError
from app.services import jwks as jwks_svc
from app.services import oidc as oidc_svc

from ._oidc_helpers import (
    install_jwks_mock,
    make_claims,
    patch_exchange,
    sign_id_token,
)


@pytest.mark.asyncio
async def test_wrong_signature_refused(make_provider, db, monkeypatch):
    p = make_provider()
    # Sign with an unrelated key — install_jwks_mock publishes a different key.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    install_jwks_mock(monkeypatch)
    bad_token = sign_id_token(
        make_claims(p, sub="x", email="x@example.com"), key=other
    )

    async def fake_exchange(_p, _c, kind="login"):
        return {"id_token": bad_token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_BAD_SIGNATURE"


@pytest.mark.asyncio
async def test_unknown_kid_refused(make_provider, db, monkeypatch):
    p = make_provider()
    install_jwks_mock(monkeypatch, kid="kid-1")
    # Sign with a kid the JWKS mock doesn't publish.
    token = sign_id_token(
        make_claims(p, sub="x", email="x@example.com"), kid="kid-rotated"
    )

    async def fake_exchange(_p, _c, kind="login"):
        return {"id_token": token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_KEY_NOT_FOUND"


@pytest.mark.asyncio
async def test_algorithm_none_refused(make_provider, db, monkeypatch):
    """Manually-built `alg=none` token must be refused at the
    algorithm allowlist gate before any signature work."""
    import base64
    import json

    p = make_provider()
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT","kid":"k"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(make_claims(p, sub="x", email="x@example.com")).encode()
    ).rstrip(b"=").decode()
    token = f"{header}.{payload}."

    install_jwks_mock(monkeypatch)

    async def fake_exchange(_p, _c, kind="login"):
        return {"id_token": token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_BAD_ID_TOKEN"


@pytest.mark.asyncio
async def test_hs256_downgrade_refused(make_provider, db, monkeypatch):
    """An attacker tries to swap an asymmetric token for an HS256 one
    using the public key as the HMAC secret. Rejected by the algorithm
    allowlist."""
    p = make_provider()
    install_jwks_mock(monkeypatch)
    token = jwt.encode(
        make_claims(p, sub="x", email="x@example.com"),
        "long-enough-hmac-secret-for-pyjwt-recommendation-32",
        algorithm="HS256",
        headers={"kid": "kid-1"},
    )

    async def fake_exchange(_p, _c, kind="login"):
        return {"id_token": token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_BAD_ID_TOKEN"


@pytest.mark.asyncio
async def test_expired_token_refused(make_provider, db, monkeypatch):
    p = make_provider()
    now = int(time.time())
    claims = make_claims(p, sub="x", email="x@example.com")
    claims["iat"] = now - 1200
    claims["exp"] = now - 600  # expired 10 minutes ago
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_nonce_mismatch_refused(make_provider, make_user, db, monkeypatch):
    from app.models.user import UserRole

    p = make_provider()
    make_user(email="bob@example.com", role=UserRole.client)
    claims = make_claims(p, sub="x", email="bob@example.com")
    claims["nonce"] = "issued-nonce"
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce="completely-different-nonce",
        )
    assert exc.value.code == "OIDC_BAD_NONCE"


@pytest.mark.asyncio
async def test_jwks_unavailable_surfaces_503(make_provider, db, monkeypatch):
    """If the JWKS fetch helper raises (network down), the user sees a
    distinct error code instead of OIDC_BAD_SIGNATURE."""
    p = make_provider()

    async def boom(_provider, _kid):
        raise AppError(503, "OIDC_JWKS_UNAVAILABLE", "boom")

    monkeypatch.setattr(jwks_svc, "get_signing_key", boom)
    token = sign_id_token(make_claims(p, sub="x", email="x@example.com"))

    async def fake_exchange(_p, _c, kind="login"):
        return {"id_token": token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_JWKS_UNAVAILABLE"
    assert exc.value.status_code == 503
