"""JWKS cache + IdP key-rotation handling.

Pure unit coverage — we don't talk to a real IdP. `_fetch_jwks` and
`oidc._discovery` are monkeypatched per test so we exercise the cache
state machine without httpx round-trips.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.oidc_provider import OIDCPreset, OIDCProvider
from app.services import jwks as jwks_svc
from app.services import oidc as oidc_svc


def _make_provider() -> OIDCProvider:
    return OIDCProvider(
        id="prov-1",
        name="Test Provider",
        preset=OIDCPreset.custom,
        issuer_url="https://idp.example.com",
        client_id="cli",
        client_secret_encrypted="x",
        groups_claim="groups",
        admin_groups="",
        employee_groups="",
        redirect_uri="",
        enabled=True,
    )


class _StubKey:
    """Stand-in for jwt.PyJWK — we don't decode anything, just check
    that the right key gets returned for the right kid."""
    def __init__(self, marker: str):
        self.key = marker


@pytest.mark.asyncio
async def test_first_call_populates_cache(monkeypatch):
    provider = _make_provider()

    async def fake_discovery(_p):
        return {"jwks_uri": "https://idp.example.com/jwks"}

    fetched: list[str] = []

    async def fake_fetch(uri):
        fetched.append(uri)
        return {"kid-a": _StubKey("key-a")}

    monkeypatch.setattr(oidc_svc, "_discovery", fake_discovery)
    monkeypatch.setattr(jwks_svc, "_fetch_jwks", fake_fetch)

    key = await jwks_svc.get_signing_key(provider, "kid-a")
    assert key == "key-a"
    assert fetched == ["https://idp.example.com/jwks"]
    assert provider.id in jwks_svc._cache


@pytest.mark.asyncio
async def test_unknown_kid_triggers_refresh(monkeypatch):
    provider = _make_provider()

    async def fake_discovery(_p):
        return {"jwks_uri": "https://idp.example.com/jwks"}

    call_count = {"n": 0}

    async def fake_fetch(_uri):
        call_count["n"] += 1
        # First call: only kid-a. Second call (after rotation): adds kid-b.
        if call_count["n"] == 1:
            return {"kid-a": _StubKey("key-a")}
        return {"kid-a": _StubKey("key-a"), "kid-b": _StubKey("key-b")}

    monkeypatch.setattr(oidc_svc, "_discovery", fake_discovery)
    monkeypatch.setattr(jwks_svc, "_fetch_jwks", fake_fetch)

    # First call caches kid-a.
    assert await jwks_svc.get_signing_key(provider, "kid-a") == "key-a"
    assert call_count["n"] == 1

    # Now ask for kid-b — cache miss triggers refresh, which picks up the new key.
    assert await jwks_svc.get_signing_key(provider, "kid-b") == "key-b"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_still_unknown_kid_after_refresh_raises(monkeypatch):
    provider = _make_provider()

    async def fake_discovery(_p):
        return {"jwks_uri": "https://idp.example.com/jwks"}

    async def fake_fetch(_uri):
        return {"kid-a": _StubKey("key-a")}  # never has kid-z

    monkeypatch.setattr(oidc_svc, "_discovery", fake_discovery)
    monkeypatch.setattr(jwks_svc, "_fetch_jwks", fake_fetch)

    with pytest.raises(AppError) as exc:
        await jwks_svc.get_signing_key(provider, "kid-z")
    assert exc.value.code == "OIDC_KEY_NOT_FOUND"


@pytest.mark.asyncio
async def test_empty_kid_raises_without_fetch(monkeypatch):
    provider = _make_provider()

    async def fake_fetch(_uri):
        raise AssertionError("must not fetch on empty kid")

    monkeypatch.setattr(jwks_svc, "_fetch_jwks", fake_fetch)

    with pytest.raises(AppError) as exc:
        await jwks_svc.get_signing_key(provider, "")
    assert exc.value.code == "OIDC_BAD_ID_TOKEN"


@pytest.mark.asyncio
async def test_missing_jwks_uri_in_discovery_raises(monkeypatch):
    provider = _make_provider()

    async def fake_discovery(_p):
        return {}  # no jwks_uri

    monkeypatch.setattr(oidc_svc, "_discovery", fake_discovery)
    jwks_svc._reset_cache()

    with pytest.raises(AppError) as exc:
        await jwks_svc.get_signing_key(provider, "kid-a")
    assert exc.value.code == "OIDC_BAD_DISCOVERY"


@pytest.mark.asyncio
async def test_jwks_endpoint_http_error_raises(monkeypatch):
    """When httpx fails on the JWKS endpoint, _fetch_jwks raises
    OIDC_JWKS_UNAVAILABLE — not silently caching an empty key set."""
    provider = _make_provider()

    async def fake_discovery(_p):
        return {"jwks_uri": "https://idp.example.com/jwks"}

    import httpx

    async def fake_fetch(_uri):
        raise AppError(503, "OIDC_JWKS_UNAVAILABLE", "boom")

    monkeypatch.setattr(oidc_svc, "_discovery", fake_discovery)
    monkeypatch.setattr(jwks_svc, "_fetch_jwks", fake_fetch)

    with pytest.raises(AppError) as exc:
        await jwks_svc.get_signing_key(provider, "kid-a")
    assert exc.value.code == "OIDC_JWKS_UNAVAILABLE"

    _ = httpx  # silence ruff


def test_reset_cache_clears(monkeypatch):
    jwks_svc._cache["x"] = (0.0, {})
    jwks_svc._reset_cache()
    assert jwks_svc._cache == {}
