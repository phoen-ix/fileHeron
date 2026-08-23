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


# --- the body is capped BEFORE it is buffered --------------------------------


@pytest.mark.asyncio
async def test_an_oversized_csp_report_is_refused_without_reading_the_body(
    client, monkeypatch
):
    """`await request.body()` materialises the whole body, so a length check
    AFTER it has already paid the cost it exists to avoid - and nginx allows
    1024m on /api/ for the direct-upload path. The edge now caps
    /api/telemetry/ at 64k; this is the in-process half.

    Asserted by proving the read never happens, not merely that the response is
    204 - the handler answers 204 on every path, so a status assertion alone
    would pass whatever it did."""
    from starlette.requests import Request

    from app.services import error_log

    monkeypatch.setattr(error_log, "log_enabled_cached", lambda: True)

    read = {"called": False}
    real_body = Request.body

    async def _spy(self):
        read["called"] = True
        return await real_body(self)

    monkeypatch.setattr(Request, "body", _spy)

    r = await client.post(
        "/api/telemetry/csp-report",
        content=b"x" * 9000,
        headers={"content-type": "application/csp-report"},
    )
    assert r.status_code == 204
    assert read["called"] is False, (
        "the oversized body was buffered before being rejected"
    )


@pytest.mark.asyncio
async def test_a_normal_sized_report_is_still_read(client, monkeypatch):
    """The control. Without it the guard above is satisfied by a handler that
    stopped reading bodies altogether, i.e. by the sink going dark."""
    import json

    from starlette.requests import Request

    from app.services import error_log

    monkeypatch.setattr(error_log, "log_enabled_cached", lambda: True)

    read = {"called": False}
    real_body = Request.body

    async def _spy(self):
        read["called"] = True
        return await real_body(self)

    monkeypatch.setattr(Request, "body", _spy)

    payload = json.dumps(
        {"csp-report": {"effective-directive": "script-src",
                        "blocked-uri": "https://evil.test/x.js"}}
    ).encode()
    r = await client.post(
        "/api/telemetry/csp-report",
        content=payload,
        headers={"content-type": "application/csp-report"},
    )
    assert r.status_code == 204
    assert read["called"] is True, "a normal report was never read"
