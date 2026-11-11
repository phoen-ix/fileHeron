"""On-demand cron trigger + live-check re-run on /api/admin/system.

- POST /system/crons/{job}/run enqueues a known cron (allowlisted),
  audits cron_run_triggered, and is admin-only.
- GET /system/live returns the liveness probes with a checked_at stamp; a
  failed probe answers its exception class only, the message goes to the log.
"""
from __future__ import annotations

import logging

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


async def _admin_headers(make_user, db, login_as, email="admin@test.local"):
    admin = make_user(email=email, role=UserRole.admin)
    token, _ = await login_as(email, "TestPassword123!")
    return admin, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_run_cron_enqueues_and_audits(client, db, make_user, login_as, monkeypatch):
    admin, headers = await _admin_headers(make_user, db, login_as)

    # Spy over the autouse no-op aenqueue so we can assert the dispatch.
    calls: list[tuple] = []

    async def _spy(name, *args, **kwargs):
        calls.append((name, args, kwargs))

    from app.services import job_queue
    monkeypatch.setattr(job_queue, "aenqueue", _spy)

    r = await client.post("/api/admin/system/crons/expire_files/run", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"job_name": "expire_files", "queued": True}
    assert calls == [("expire_files", (), {})]

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.cron_run_triggered.value)
        .one()
    )
    assert audit.actor_user_id == admin.id
    assert audit.target_id == "expire_files"


@pytest.mark.asyncio
async def test_run_cron_rejects_unknown_job(client, db, make_user, login_as):
    _admin, headers = await _admin_headers(make_user, db, login_as)
    r = await client.post("/api/admin/system/crons/rm_minus_rf/run", headers=headers)
    assert r.status_code == 404
    assert r.json()["code"] == "CRON_UNKNOWN"


@pytest.mark.asyncio
async def test_run_cron_requires_admin(client, db, make_user, login_as):
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")
    r = await client.post(
        "/api/admin/system/crons/expire_files/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_live_endpoint_has_checked_at_and_probes(client, db, make_user, login_as):
    _admin, headers = await _admin_headers(make_user, db, login_as)
    r = await client.get("/api/admin/system/live", headers=headers)
    assert r.status_code == 200, r.text
    live = r.json()["live"]
    assert live["checked_at"]  # ISO timestamp present
    for probe in ("db", "redis", "av"):
        assert probe in live
        assert "status" in live[probe]


@pytest.mark.asyncio
async def test_a_failed_probe_answers_its_class_and_logs_the_message(
    client, db, make_user, login_as, monkeypatch, caplog
):
    _admin, headers = await _admin_headers(make_user, db, login_as)
    detail = "connect to redis-internal.example:6379 refused"

    class _DeadRedis:
        def ping(self):
            raise ConnectionError(detail)

    def _av_ping():
        raise TimeoutError(detail)

    from app.config import settings
    from app.routers.admin import system
    from app.services import av_scan

    monkeypatch.setattr(system, "get_redis", lambda: _DeadRedis())
    monkeypatch.setattr(settings, "AV_SKIP", False)
    monkeypatch.setattr(av_scan, "ping", _av_ping)

    for path in ("/api/admin/system/live", "/api/admin/system/status"):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="fileheron.admin.system"):
            r = await client.get(path, headers=headers)
        assert r.status_code == 200, r.text
        live = r.json()["live"]
        assert live["redis"] == {"status": "down", "error_type": "ConnectionError"}
        assert live["av"] == {"status": "down", "error_type": "TimeoutError"}
        assert detail not in r.text
        logged = [rec for rec in caplog.records if rec.name == "fileheron.admin.system"]
        assert [str(rec.exc_info[1]) for rec in logged] == [detail, detail]
