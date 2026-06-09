"""POST /api/telemetry/page-404 - the anonymous SPA client-404 beacon.

Opt-in (no-op unless 4xx capture is on), per-IP rate-limited, enqueues a
source="spa" 404 event into the error pipeline. Unauthenticated.
"""
from __future__ import annotations

import pytest

from app.services import error_log
from app.services import settings as settings_svc

K = settings_svc.Keys


def _enable_capture(db):
    settings_svc.set_value(db, key=K.ERROR_LOG_CAPTURE_4XX, value="true", actor=None)
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="404", actor=None)
    db.commit()
    error_log._reset_cache()


@pytest.mark.asyncio
async def test_beacon_enqueues_spa_event_when_capture_on(db, client, monkeypatch):
    _enable_capture(db)
    calls: list = []
    from app.services import job_queue
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))

    r = await client.post("/api/telemetry/page-404", json={"path": "/foobar?x=1"})
    assert r.status_code == 204
    assert len(calls) == 1
    name, kw = calls[0]
    assert name == "notify_admin_error"
    ev = kw["event"]
    assert ev["source"] == "spa"
    assert ev["status_code"] == 404
    assert ev["code"] == "NOT_FOUND"
    assert ev["path"] == "/foobar"  # query stripped


@pytest.mark.asyncio
async def test_beacon_noop_when_capture_off(db, client, monkeypatch):
    # capture_4xx defaults off -> cached gate returns False -> no enqueue.
    error_log._reset_cache()
    calls: list = []
    from app.services import job_queue
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))

    r = await client.post("/api/telemetry/page-404", json={"path": "/foobar"})
    assert r.status_code == 204
    assert calls == []


@pytest.mark.asyncio
async def test_beacon_rate_limited(db, client, monkeypatch):
    _enable_capture(db)
    from app.services import job_queue, rate_limit
    calls: list = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))
    # Force the per-IP guard to deny.
    monkeypatch.setattr(rate_limit, "check_ip_allowed", lambda *a, **k: False)

    r = await client.post("/api/telemetry/page-404", json={"path": "/foobar"})
    assert r.status_code == 204
    assert calls == []
