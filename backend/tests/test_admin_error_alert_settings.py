"""GET/PUT /api/admin/settings/error-alerts + the per-task alert_on_failure
toggle on /api/admin/crons (error log + email-on-error feature)."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole

_PW = "Pass12345678!"


def _payload(**over):
    """A complete, valid update payload; override individual fields per test."""
    base = {
        "enabled": True,
        "source_http_5xx": True,
        "source_http_4xx": False,
        "recipients_mode": "admins",
        "custom_recipients": [],
        "cooldown_minutes": 15,
        "max_per_hour": 20,
        "log_enabled": True,
        "capture_4xx": False,
        "http_4xx_codes": [],
        "retention_days": 90,
    }
    base.update(over)
    return base


async def _admin_token(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin, password=_PW)
    token, _ = await login_as(email, _PW)
    return token


@pytest.mark.asyncio
async def test_get_defaults(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.get(
        "/api/admin/settings/error-alerts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["source_http_5xx"] is True
    assert body["source_http_4xx"] is False
    assert body["recipients_mode"] == "admins"
    assert body["custom_recipients"] == []
    assert body["cooldown_minutes"] == 15
    assert body["max_per_hour"] == 20
    # Logging defaults: on for 5xx, 4xx capture off, 90d retention.
    assert body["log_enabled"] is True
    assert body["capture_4xx"] is False
    assert body["http_4xx_codes"] == []
    assert body["retention_days"] == 90


@pytest.mark.asyncio
async def test_put_round_trip_and_audit(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    payload = _payload(
        recipients_mode="custom",
        custom_recipients=["ops@corp.local", "sre@corp.local"],
        cooldown_minutes=30,
        max_per_hour=5,
        capture_4xx=True,
        http_4xx_codes=[429, 409],
        source_http_4xx=True,
        retention_days=30,
    )
    resp = await client.put(
        "/api/admin/settings/error-alerts",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["recipients_mode"] == "custom"
    assert body["custom_recipients"] == ["ops@corp.local", "sre@corp.local"]
    assert body["cooldown_minutes"] == 30
    assert body["max_per_hour"] == 5
    assert body["capture_4xx"] is True
    assert body["http_4xx_codes"] == [409, 429]  # normalised + sorted
    assert body["source_http_4xx"] is True
    assert body["retention_days"] == 30

    # GET reflects it.
    again = await client.get(
        "/api/admin/settings/error-alerts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again.json()["max_per_hour"] == 5
    assert again.json()["http_4xx_codes"] == [409, 429]

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.error_alert_settings_changed.value)
        .all()
    )
    assert len(rows) == 1
    # Count is recorded; the addresses themselves never are.
    assert rows[0].extra["recipient_count"] == 2
    assert rows[0].extra["capture_4xx"] is True
    assert rows[0].extra["http_4xx_code_count"] == 2
    assert "ops@corp.local" not in str(rows[0].extra)


@pytest.mark.asyncio
async def test_put_normalises_4xx_codes(make_user, client, login_as):
    """Out-of-range / non-4xx codes are dropped; only valid 4xx are stored."""
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/error-alerts",
        json=_payload(capture_4xx=True, http_4xx_codes=[429, 500, 99, 404]),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["http_4xx_codes"] == [404, 429]


@pytest.mark.asyncio
async def test_put_rejects_bad_email(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/error-alerts",
        json=_payload(recipients_mode="custom", custom_recipients=["not-an-email"]),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_out_of_range_cooldown(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/error-alerts",
        json=_payload(cooldown_minutes=0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password=_PW)
    token, _ = await login_as("c@test.local", _PW)
    resp = await client.get(
        "/api/admin/settings/error-alerts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cron_alert_on_failure_round_trip(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    # Enable the master feature so the SPA gate flag flips.
    await client.put(
        "/api/admin/settings/error-alerts",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    # Set the per-task flag for one cron.
    resp = await client.put(
        "/api/admin/crons/expire_files",
        json={
            "enabled": True, "kind": "interval", "interval_minutes": 60,
            "daily_time": "02:00", "alert_on_failure": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["alert_on_failure"] is True

    listing = await client.get(
        "/api/admin/crons", headers={"Authorization": f"Bearer {token}"}
    )
    data = listing.json()
    assert data["error_alerts_enabled"] is True
    item = next(i for i in data["items"] if i["name"] == "expire_files")
    assert item["alert_on_failure"] is True
    other = next(i for i in data["items"] if i["name"] != "expire_files")
    assert other["alert_on_failure"] is False
