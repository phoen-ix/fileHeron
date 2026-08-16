"""Admin surface for the scan guard."""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"
SCANNER = "45.148.10.67"


def _payload(**over):
    body = {
        "enabled": True,
        "signal_probe_path": True,
        "signal_api_404": False,
        "signal_auth_failure": False,
        "escalation": True,
        "network_escalation": False,
        # `digest` was removed - it had no backend consumer for a whole release,
        # an inert control on the page that refuses inert configurations.
        "notify_mode": "off",
        "allowlist": "",
        "extra_paths": "",
        "ignore_paths": "",
        "threshold": 3,
        "window_sec": 3600,
        "block_minutes": 60,
        "max_block_minutes": 1440,
        "min_distinct_paths": 15,
        "network_threshold": 3,
        "network_lookback_hours": 168,
        "max_new_blocks_per_min": 60,
        "network_prefix_v6": 64,
    }
    body.update(over)
    return body


async def _admin_headers(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_a_virgin_instance_reports_the_guard_off(make_user, client, login_as):
    """The upgrade must be behaviour-neutral: nothing is blocked until an admin
    opts in."""
    headers = await _admin_headers(make_user, login_as)
    body = (await client.get("/api/admin/scan-guard", headers=headers)).json()
    assert body["enabled"] is False
    assert body["network_escalation"] is False
    assert body["signal_auth_failure"] is False
    assert body["active_ip_blocks"] == 0


@pytest.mark.asyncio
async def test_settings_round_trip(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard", json=_payload(threshold=9), headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["threshold"] == 9
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_a_stored_digest_reads_as_off(db, make_user, client, login_as):
    """An instance that stored `digest` before v2.11.0 removed the mode must not
    fall through to a mode that no longer exists. It reads as `off` - which is
    silent, so the coercion is also why an upgraded instance stops notifying
    without saying so, and the admin sees `off` on the page rather than a value
    the backend cannot honour."""
    from app.services import scan_guard as sg
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.SCAN_GUARD_NOTIFY_MODE, value="digest", actor=None
    )
    db.commit()

    assert sg.get_settings(db)["notify_mode"] == "off"

    headers = await _admin_headers(make_user, login_as)
    body = (await client.get("/api/admin/scan-guard", headers=headers)).json()
    assert body["notify_mode"] == "off"


@pytest.mark.asyncio
async def test_digest_is_refused_on_write(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard", json=_payload(notify_mode="digest"), headers=headers
    )
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_enabling_with_no_signals_is_refused(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard",
        json=_payload(signal_probe_path=False),
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SCAN_GUARD_NO_SIGNALS"


@pytest.mark.asyncio
async def test_a_bad_allowlist_is_refused(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard", json=_payload(allowlist="10.0.0.0/8, junk"),
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "ALLOWLIST_INVALID"


@pytest.mark.asyncio
async def test_a_manual_block_refuses_a_non_global_subject(
    make_user, client, login_as
):
    """The manual endpoint must not be a way around the invariant - blocking the
    compose bridge or loopback would take out the frontend, tusd and the
    healthcheck."""
    headers = await _admin_headers(make_user, login_as)
    for subject in ("127.0.0.1", "10.0.0.5", "192.168.0.0/16"):
        resp = await client.post(
            "/api/admin/scan-guard/blocks",
            json={"subject": subject, "minutes": 60},
            headers=headers,
        )
        assert resp.status_code == 400, subject
        assert resp.json()["code"] == "SUBJECT_NOT_BLOCKABLE"


@pytest.mark.asyncio
async def test_manual_block_then_release(make_user, db, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    created = await client.post(
        "/api/admin/scan-guard/blocks",
        json={"subject": SCANNER, "minutes": 30, "note": "seen in the log"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    block_id = created.json()["id"]
    assert created.json()["source"] == "manual"

    listed = await client.get("/api/admin/scan-guard/blocks", headers=headers)
    assert [r["id"] for r in listed.json()["items"]] == [block_id]

    gone = await client.delete(
        f"/api/admin/scan-guard/blocks/{block_id}", headers=headers
    )
    assert gone.status_code == 204
    still = await client.get("/api/admin/scan-guard/blocks", headers=headers)
    assert still.json()["items"] == []
    # Released, not deleted: the row remains as history.
    with_expired = await client.get(
        "/api/admin/scan-guard/blocks?active=false", headers=headers
    )
    assert [r["id"] for r in with_expired.json()["items"]] == [block_id]


@pytest.mark.asyncio
async def test_releasing_an_unknown_block_404s(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.delete("/api/admin/scan-guard/blocks/424242", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "IP_BLOCK_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_non_admin_cannot_reach_any_of_it(make_user, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/admin/scan-guard", headers=headers)).status_code == 403
    assert (
        await client.get("/api/admin/scan-guard/blocks", headers=headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_settings_audit_records_keys_not_values(
    make_user, db, client, login_as
):
    from app.models.audit_log import AuditEventType, AuditLog

    headers = await _admin_headers(make_user, login_as)
    await client.put(
        "/api/admin/scan-guard", json=_payload(allowlist=f"{SCANNER}/32"),
        headers=headers,
    )
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.scan_guard_settings_changed.value)
        .one()
    )
    assert "keys" in row.extra
    assert SCANNER not in str(row.extra), "an audit row must not carry the values"
