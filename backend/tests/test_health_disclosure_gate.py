"""The build identifiers must not reach an anonymous caller.

Audit #2, three findings on one control.

`/api/health` hides `running_version`, `running_sha` and the `degraded` list
from public callers: fileHeron is published for self-hosting against a public
repo, so a SHA maps one-to-one onto a known source tree and says exactly which
security fixes an instance is missing, and `degraded` announces that Redis is
down and the per-IP limiter has fallen back to the weaker in-process one -
precisely when to start credential stuffing.

Three things were wrong with it:

1. Nothing tested the gate. Deleting the two lines that implement it - a
   plausible outcome of any refactor - left the whole suite green (measured).
   The one test that named the area asserted the OPPOSITE property.
2. `_peer_is_operator` accepted every RFC1918 address, not "loopback or the
   compose network" as its docstring said. On a host whose LAN is 192.168/24,
   or behind a proxy forwarding a private client address, the diagnostic body
   went to callers that are not operators.
3. `/api/config-public` - anonymous by design - handed out `running_version`
   anyway, so the gate could be walked around by asking a different endpoint.
"""
from __future__ import annotations

import pytest


class _Req:
    def __init__(self, host: str | None):
        self.client = type("C", (), {"host": host})() if host else None


def _clear_cache():
    from app.routers import health

    cache = getattr(health, "_trusted_networks", None)
    if cache is not None and hasattr(cache, "cache_clear"):
        cache.cache_clear()


@pytest.fixture(autouse=True)
def _clear_network_cache():
    _clear_cache()
    yield
    _clear_cache()


def test_loopback_is_an_operator():
    from app.routers.health import _peer_is_operator

    assert _peer_is_operator(_Req("127.0.0.1")) is True
    assert _peer_is_operator(_Req("::1")) is True


def test_a_public_address_is_not(monkeypatch):
    from app.routers.health import _peer_is_operator

    assert _peer_is_operator(_Req("8.8.8.8")) is False
    # Python counts the RFC 5737 documentation ranges as `is_private`, so the
    # old check called this address an operator too.
    assert _peer_is_operator(_Req("203.0.113.9")) is False
    assert _peer_is_operator(_Req(None)) is False
    assert _peer_is_operator(_Req("not-an-ip")) is False


def test_a_home_lan_address_is_not_an_operator(monkeypatch):
    """The behaviour the old `addr.is_private` gave away. A reverse proxy on a
    192.168/24 LAN forwarding a client address made every visitor an
    "operator"."""
    from app.routers import health

    monkeypatch.setenv("HEALTH_DETAIL_TRUSTED_CIDRS", "172.19.0.0/16")
    _clear_cache()
    assert health._peer_is_operator(_Req("192.168.1.50")) is False
    assert health._peer_is_operator(_Req("10.4.4.4")) is False
    assert health._peer_is_operator(_Req("172.19.0.7")) is True


@pytest.mark.asyncio
async def test_an_anonymous_health_probe_gets_liveness_only(client, monkeypatch):
    """The gate itself, end to end. The ASGI test transport reports a loopback
    peer, which IS an operator - so the public caller is simulated at the
    predicate, whose own behaviour the tests above pin."""
    from app.routers import health

    monkeypatch.setattr(health, "_peer_is_operator", lambda _r: False)
    r = await client.get("/api/health")
    assert r.status_code in (200, 503)
    body = r.json()
    assert body.get("status")
    for leaked in ("running_sha", "degraded", "db_latency_ms"):
        assert leaked not in body, f"{leaked} disclosed to an anonymous caller"


@pytest.mark.asyncio
async def test_an_operator_probe_gets_the_diagnostics(client, monkeypatch):
    """The control: the compose HEALTHCHECK and the updater's running_version
    poll both depend on this half."""
    from app.routers import health

    monkeypatch.setattr(health, "_peer_is_operator", lambda _r: True)
    r = await client.get("/api/health")
    body = r.json()
    assert "running_version" in body
    assert "running_sha" in body


@pytest.mark.asyncio
async def test_config_public_does_not_hand_out_the_version(client):
    """The same fact through a different door. Nothing in the SPA rendered it -
    the admin surface reads its version from /api/admin/system/status."""
    r = await client.get("/api/config-public")
    assert r.status_code == 200
    assert "running_version" not in r.json()


def test_the_gate_is_still_wired_into_the_route():
    """Guards the deletion the sweep proved the suite could not see."""
    import inspect

    from app.routers import health

    src = inspect.getsource(health.health_check)
    assert "_peer_is_operator" in src
    assert "if not detailed:" in src
