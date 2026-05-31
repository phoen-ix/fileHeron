"""On-demand cron trigger + live-check re-run on /api/admin/system.

- POST /system/crons/{job}/run enqueues a known cron (allowlisted),
  audits cron_run_triggered, and is admin-only.
- GET /system/live returns the liveness probes with a checked_at stamp.
"""
from __future__ import annotations

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
