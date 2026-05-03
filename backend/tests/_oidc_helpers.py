"""Shared test helpers for OIDC flow tests.

Generates an RSA keypair once per test (cheap enough at 2048 bits) and
exposes:

- ``sign_id_token(claims, *, kid="kid-1", alg="RS256")`` — produces a
  signed JWT under the test private key.
- ``install_jwks_mock(monkeypatch, *, kid="kid-1")`` — patches
  ``services.jwks.get_signing_key`` so verification finds the test
  public key.
- ``patch_exchange(monkeypatch, claims, *, kid="kid-1", alg="RS256")``
  — single-call helper that wires both the token-endpoint stub and the
  JWKS mock for the most common test pattern (one fake login round
  trip).

A fresh keypair per test means the JWKS cache reset autouse in
``conftest`` keeps tests independent."""
from __future__ import annotations

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


def make_claims(provider, *, sub: str, email: str, email_verified: bool = True, **extra: Any) -> dict[str, Any]:
    """Build a base claims dict with iss/aud/exp/iat already filled in
    against the given provider — pyjwt's `require` rejects tokens
    missing exp/iat, so every test claim set needs them."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": provider.issuer_url.rstrip("/"),
        "aud": provider.client_id,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "exp": now + 600,
    }
    claims.update(extra)
    return claims

# 2048-bit RSA: ~30ms to generate. Cached across tests in this module
# so the cost is paid once even when many tests import it.
_PRIV_KEY: rsa.RSAPrivateKey | None = None


def _key() -> rsa.RSAPrivateKey:
    global _PRIV_KEY
    if _PRIV_KEY is None:
        _PRIV_KEY = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
    return _PRIV_KEY


def sign_id_token(
    claims: dict[str, Any],
    *,
    kid: str = "kid-1",
    alg: str = "RS256",
    key: rsa.RSAPrivateKey | None = None,
) -> str:
    """Sign claims into a JWT with our test private key (or an override
    key — used by the wrong-signature test case)."""
    return jwt.encode(
        claims,
        key or _key(),
        algorithm=alg,
        headers={"kid": kid, "alg": alg},
    )


def install_jwks_mock(monkeypatch, *, kid: str = "kid-1") -> None:
    """Patch ``services.jwks.get_signing_key`` to return our test
    public key for the named kid; raise OIDC_KEY_NOT_FOUND otherwise.
    Mirrors the production helper's behaviour without going through
    httpx."""
    from app.middleware.errors import AppError
    from app.services import jwks as jwks_svc

    pub = _key().public_key()

    async def fake_get_signing_key(_provider, asked_kid):
        if asked_kid != kid:
            raise AppError(
                401,
                "OIDC_KEY_NOT_FOUND",
                "ID token was signed with a key the identity provider does not advertise.",
            )
        return pub

    monkeypatch.setattr(jwks_svc, "get_signing_key", fake_get_signing_key)


def patch_exchange(
    monkeypatch,
    claims: dict[str, Any],
    *,
    kid: str = "kid-1",
    alg: str = "RS256",
    key: rsa.RSAPrivateKey | None = None,
) -> None:
    """One-shot: install JWKS mock + stub _exchange_code to return a
    signed token bearing `claims`. Most existing tests only need this."""
    from app.services import oidc as oidc_svc

    install_jwks_mock(monkeypatch, kid=kid)
    token = sign_id_token(claims, kid=kid, alg=alg, key=key)

    async def fake_exchange(_provider, _code, kind="login"):
        return {"id_token": token}

    monkeypatch.setattr(oidc_svc, "_exchange_code", fake_exchange)
    oidc_svc.reset_discovery_cache()
