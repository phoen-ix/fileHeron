"""Admin scheduled-tasks (cron) endpoints (v1.28.0)."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import settings as s

PW = "Pass12345678!"


async def _admin_token(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    return (await login_as("admin@test.local", PW))[0]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.asyncio
async def test_list_crons(make_user, client, login_as):
    t = await _admin_token(make_user, login_as)
    r = await client.get("/api/admin/crons", headers=_h(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 19
    names = {i["name"] for i in body["items"]}
    assert "expire_files" in names and "imap_poll" in names
    assert "rescan_inbound_attachments" in names  # audit L18
    ef = next(i for i in body["items"] if i["name"] == "expire_files")
    assert ef["kind"] == "interval" and ef["interval_minutes"] == 60 and ef["enabled"] is True


@pytest.mark.asyncio
async def test_update_cron_persists_and_audits(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    r = await client.put(
        "/api/admin/crons/expire_files",
        json={"enabled": True, "kind": "interval", "interval_minutes": 15, "daily_time": "02:00"},
        headers=_h(t),
    )
    assert r.status_code == 200, r.text
    assert r.json()["interval_minutes"] == 15
    assert s.get(db, "cron.expire_files.interval_minutes") == "15"
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.cron_schedule_changed)
        .one()
    )
    assert audit.target_id == "expire_files"


@pytest.mark.asyncio
async def test_update_cron_disable(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    r = await client.put(
        "/api/admin/crons/prune_history",
        json={"enabled": False, "kind": "daily", "interval_minutes": 1440, "daily_time": "03:30"},
        headers=_h(t),
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["daily_time"] == "03:30"


@pytest.mark.asyncio
async def test_update_rejects_unknown_and_bad_time(make_user, client, login_as):
    t = await _admin_token(make_user, login_as)
    assert (
        await client.put("/api/admin/crons/nope",
                         json={"enabled": True, "kind": "interval", "interval_minutes": 60, "daily_time": "02:00"},
                         headers=_h(t))
    ).status_code == 404
    bad = await client.put(
        "/api/admin/crons/expire_files",
        json={"enabled": True, "kind": "daily", "interval_minutes": 60, "daily_time": "25:99"},
        headers=_h(t),
    )
    assert bad.status_code == 422  # pattern validation


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    t = (await login_as("emp@test.local", PW))[0]
    assert (await client.get("/api/admin/crons", headers=_h(t))).status_code in (401, 403)
