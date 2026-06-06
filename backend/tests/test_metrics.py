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
