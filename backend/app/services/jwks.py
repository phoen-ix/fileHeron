"""Per-provider JWKS fetch + cache for OIDC ID-token signature verification.

The IdP's discovery doc advertises a `jwks_uri` returning a JSON Web Key
Set. We fetch + cache that set per provider; on verification we look up
the signing key by its `kid` header. If we don't have it, we refresh
once (handles IdP key rotation) and look again.

Cache lives in-process (one dict per worker). Keys are public; cache
miss = one extra HTTPS round-trip; JWKS endpoints are well-cached at
the CDN edge so foreground refresh is fine at our scale. Per-provider
TTL bounds staleness when an IdP rotates without bumping kid.

Tests reset the cache via `_reset_cache()` (see conftest autouse).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import jwt

from ..middleware.errors import AppError
from ..models.oidc_provider import OIDCProvider
from ..utils.net import assert_public_http_url
from . import oidc as oidc_svc

logger = logging.getLogger("fileheron.jwks")

_CACHE_TTL_SEC = 3600  # 1 hour
# A real JWKS is a few KB. Cap the response so a malicious / compromised IdP
# can't OOM a worker by returning a multi-GB body. 1 MiB is generous.
_JWKS_MAX_BYTES = 1 * 1024 * 1024

# (provider_id) → (fetched_at, {kid: PyJWK})
_cache: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}


def _reset_cache() -> None:
    """Test hook + provider-edit hook."""
    _cache.clear()


async def _fetch_jwks(jwks_uri: str) -> dict[str, jwt.PyJWK]:
    # SSRF guard (allow private LAN IdPs; block loopback/metadata) + a hard
    # byte cap streamed off the wire so a huge body can't exhaust memory.
    assert_public_http_url(jwks_uri, allow_private=True, require_https=False)
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli, cli.stream("GET", jwks_uri) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("content-length")
            if cl is not None and int(cl) > _JWKS_MAX_BYTES:
                raise AppError(
                    503, "OIDC_JWKS_TOO_LARGE", "Identity provider key set is too large."
                )
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _JWKS_MAX_BYTES:
                    raise AppError(
                        503,
                        "OIDC_JWKS_TOO_LARGE",
                        "Identity provider key set is too large.",
                    )
        doc = json.loads(bytes(buf))
    except httpx.HTTPError as e:
        logger.warning("JWKS fetch failed uri=%s: %s", jwks_uri, e)
        raise AppError(
            503, "OIDC_JWKS_UNAVAILABLE", "Identity provider key set is unreachable."
        ) from e
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("JWKS parse failed uri=%s: %s", jwks_uri, e)
        raise AppError(
            503, "OIDC_JWKS_UNAVAILABLE", "Identity provider key set is malformed."
        ) from e

    keys: dict[str, jwt.PyJWK] = {}
    for raw in doc.get("keys", []):
        kid = raw.get("kid")
        if not kid:
            # OIDC IdPs in the wild always set kid; skip any rogue
            # entry rather than guessing the binding.
            continue
        try:
            keys[kid] = jwt.PyJWK(raw)
        except (jwt.InvalidKeyError, jwt.PyJWKError) as e:
            logger.warning("JWKS skipping unparseable key kid=%s: %s", kid, e)
    return keys


async def _load(provider: OIDCProvider) -> dict[str, jwt.PyJWK]:
    doc: dict[str, Any] = await oidc_svc._discovery(provider)
    jwks_uri = doc.get("jwks_uri")
    if not jwks_uri:
        raise AppError(
            503, "OIDC_BAD_DISCOVERY", "IdP discovery is missing jwks_uri."
        )
    keys = await _fetch_jwks(jwks_uri)
    _cache[provider.id] = (time.monotonic(), keys)
    logger.info("JWKS refreshed provider=%s key_count=%d", provider.id, len(keys))
    return keys


async def get_signing_key(provider: OIDCProvider, kid: str) -> Any:
    """Return the verifying key for `kid`. Refreshes once on miss to
    handle IdP key rotation. Raises OIDC_KEY_NOT_FOUND if still absent.
    """
    if not kid:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", "ID token missing kid header.")

    cached = _cache.get(provider.id)
    if cached is not None:
        fetched_at, keys = cached
        if (time.monotonic() - fetched_at) < _CACHE_TTL_SEC and kid in keys:
            return keys[kid].key

    # Miss, expired, or unknown kid → refresh once.
    keys = await _load(provider)
    if kid not in keys:
        raise AppError(
            401,
            "OIDC_KEY_NOT_FOUND",
            "ID token was signed with a key the identity provider does not advertise.",
        )
    return keys[kid].key
