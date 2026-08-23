"""GET /api/metrics - auth gate + Prometheus exposition."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fast_probes(monkeypatch):
    """Keep the metrics render off the network: skip the ClamAV ping and make
    the Redis cache/ping calls fail-open instantly instead of waiting on socket
    timeouts that aren't reachable from the unit-test sandbox."""
    monkeypatch.setattr("app.config.settings.AV_SKIP", True)

    def _boom(*_a, **_kw):
        raise RuntimeError("redis unavailable in tests")

    monkeypatch.setattr("app.redis_client.get_redis", _boom)


@pytest.mark.asyncio
async def test_metrics_requires_auth(client):
    resp = await client.get("/api/metrics")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_metrics_bearer_ok(client, make_user, monkeypatch):
    monkeypatch.setattr("app.config.settings.METRICS_BEARER_TOKEN", "scrape-secret-token")
    make_user(email="a@test.local")
    resp = await client.get(
        "/api/metrics", headers={"Authorization": "Bearer scrape-secret-token"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "fileheron_users_total 1" in body
    assert "# TYPE fileheron_storage_used_bytes gauge" in body
    assert "fileheron_db_status 1" in body
    assert "fileheron_redis_status 0" in body  # redis stubbed unreachable


@pytest.mark.asyncio
async def test_metrics_wrong_bearer(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.METRICS_BEARER_TOKEN", "scrape-secret-token")
    resp = await client.get("/api/metrics", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_metrics_disabled_when_unconfigured(client):
    # No bearer token and no allow-listed IPs configured → always 401.
    resp = await client.get("/api/metrics", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 401


# --- the IP allowlist -------------------------------------------------------
#
# `_ip_allowed` is the other half of the gate (`_bearer_allowed(...) or
# _ip_allowed(...)`) and nothing exercised it: every test above sets
# METRICS_BEARER_TOKEN, none ever set METRICS_ALLOWED_IPS. So the exact-address
# branch, the CIDR branch, and both `ValueError` swallows were untested on a
# route that otherwise answers 401 unconditionally.


@pytest.mark.parametrize(
    ("allowlist", "client_ip", "expected"),
    [
        ("10.0.0.5", "10.0.0.5", True),               # exact
        ("10.0.0.5", "10.0.0.6", False),
        ("10.0.0.0/24", "10.0.0.99", True),           # CIDR
        ("10.0.0.0/24", "10.0.1.1", False),
        ("10.0.0.0/24, 192.168.1.7", "192.168.1.7", True),   # second entry
        ("  10.0.0.5  ", "10.0.0.5", True),           # whitespace tolerated
        ("2001:db8::/32", "2001:db8::1", True),       # IPv6 CIDR
        ("2001:db8::/32", "2001:dbf::1", False),
        ("10.0.0.5", None, False),                    # no client address
        ("", "10.0.0.5", False),                      # empty allowlist = deny
        ("not-an-ip", "10.0.0.5", False),             # malformed entry skipped
        ("10.0.0.0/99", "10.0.0.5", False),           # malformed CIDR skipped
        ("10.0.0.5", "not-an-ip", False),             # malformed client address
    ],
)
def test_the_metrics_ip_allowlist(monkeypatch, allowlist, client_ip, expected):
    from app.routers import metrics

    monkeypatch.setattr("app.config.settings.METRICS_ALLOWED_IPS", allowlist)
    assert metrics._ip_allowed(client_ip) is expected


def test_a_malformed_entry_does_not_discard_the_rest(monkeypatch):
    """The `except ValueError: continue` is load-bearing: one typo in an
    operator's CSV must not silently deny every other scraper."""
    from app.routers import metrics

    monkeypatch.setattr(
        "app.config.settings.METRICS_ALLOWED_IPS", "oops, 10.0.0.0/24"
    )
    assert metrics._ip_allowed("10.0.0.7") is True


@pytest.mark.asyncio
async def test_metrics_served_to_an_allowlisted_address(client, monkeypatch):
    """End to end: no bearer token configured at all, allowlist alone opens it.
    The unit tests above cannot show that `_ip_allowed` is actually consulted
    by the route."""
    from app.routers import metrics

    monkeypatch.setattr("app.config.settings.METRICS_BEARER_TOKEN", "")
    monkeypatch.setattr("app.config.settings.METRICS_ALLOWED_IPS", "10.9.9.9")
    monkeypatch.setattr(metrics, "_ip_allowed", lambda _ip: True)
    r = await client.get("/api/metrics")
    assert r.status_code == 200
    assert "fileheron_users_total" in r.text
