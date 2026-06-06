"""Admin webhooks CRUD - secret shown once, admin-gated, validation."""
from __future__ import annotations

import pytest

from app.models.user import UserRole


@pytest.fixture
def admin_headers(make_user, login_as):
    async def _go():
        make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
        token, _ = await login_as("admin@test.local", "Pass12345678!")
        return {"Authorization": f"Bearer {token}"}

    return _go


@pytest.mark.asyncio
async def test_create_returns_secret_once_then_hidden(client, admin_headers):
    headers = await admin_headers()
    resp = await client.post(
        "/api/admin/webhooks",
        json={"name": "ci", "url": "https://hooks.test/x", "event_types": ["share_created"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["secret"] and len(body["secret"]) > 20  # shown once
    assert body["secret_set"] is True
    wid = body["id"]

    # List never includes the secret.
    lst = await client.get("/api/admin/webhooks", headers=headers)
    assert lst.status_code == 200
    row = next(w for w in lst.json() if w["id"] == wid)
    assert "secret" not in row
    assert row["secret_set"] is True
    assert row["event_types"] == ["share_created"]


@pytest.mark.asyncio
async def test_events_endpoint_lists_allowlist(client, admin_headers):
    headers = await admin_headers()
    r = await client.get("/api/admin/webhooks/events", headers=headers)
    assert r.status_code == 200
    events = r.json()["events"]
    assert "share_created" in events
    assert "ops.alert" in events


@pytest.mark.asyncio
async def test_patch_and_rotate_secret(client, admin_headers):
    headers = await admin_headers()
    created = (await client.post(
        "/api/admin/webhooks",
        json={"name": "a", "url": "https://h.test/a", "event_types": ["*"]},
        headers=headers,
    )).json()
    wid = created["id"]

    upd = await client.patch(
        f"/api/admin/webhooks/{wid}", json={"active": False, "name": "renamed"}, headers=headers
    )
    assert upd.status_code == 200
    assert upd.json()["active"] is False
    assert upd.json()["name"] == "renamed"
    assert "secret" not in upd.json()  # no rotate → no secret

    rot = await client.patch(
        f"/api/admin/webhooks/{wid}?rotate_secret=true", json={}, headers=headers
    )
    assert rot.status_code == 200
    assert rot.json()["secret"] != created["secret"]


@pytest.mark.asyncio
async def test_delete_test_and_deliveries(client, admin_headers):
    headers = await admin_headers()
    wid = (await client.post(
        "/api/admin/webhooks",
        json={"name": "a", "url": "https://h.test/a", "event_types": ["share_created"]},
        headers=headers,
    )).json()["id"]

    t = await client.post(f"/api/admin/webhooks/{wid}/test", headers=headers)
    assert t.status_code == 200 and t.json()["queued"] is True

    d = await client.get(f"/api/admin/webhooks/{wid}/deliveries", headers=headers)
    assert d.status_code == 200 and isinstance(d.json(), list)

    rm = await client.delete(f"/api/admin/webhooks/{wid}", headers=headers)
    assert rm.status_code == 204
    gone = await client.get("/api/admin/webhooks", headers=headers)
    assert all(w["id"] != wid for w in gone.json())


@pytest.mark.asyncio
async def test_validation_rejects_bad_url_and_event(client, admin_headers):
    headers = await admin_headers()
    bad_url = await client.post(
        "/api/admin/webhooks",
        json={"name": "x", "url": "ftp://nope", "event_types": ["share_created"]},
        headers=headers,
    )
    assert bad_url.status_code == 422
    bad_event = await client.post(
        "/api/admin/webhooks",
        json={"name": "x", "url": "https://h.test", "event_types": ["not_an_event"]},
        headers=headers,
    )
    assert bad_event.status_code == 422


@pytest.mark.asyncio
async def test_admin_only(client, make_user, login_as):
    make_user(email="client@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("client@test.local", "Pass12345678!")
    r = await client.get("/api/admin/webhooks", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
