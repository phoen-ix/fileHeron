"""Legal pages: admin per-locale edit + anonymous sanitised content + config flags."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


async def _admin_token(make_user, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    return token


@pytest.mark.asyncio
async def test_put_then_public_render_sanitised(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/legal",
        json={
            "imprint": {
                "enabled": True,
                "en": "<h1>Imprint</h1><p>Acme Ltd <script>alert(1)</script></p>",
                "de": "<h1>Impressum</h1><p>Acme GmbH</p>",
            },
            "privacy": {"enabled": False, "en": "", "de": ""},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imprint"]["enabled"] is True

    pub = await client.get("/api/legal/imprint")
    assert pub.status_code == 200
    body = pub.json()
    assert body["enabled"] is True
    assert "<h1>Imprint</h1>" in body["html_en"]
    assert "Impressum" in body["html_de"]
    # The script tag is escaped to text, never emitted as markup.
    assert "<script>" not in body["html_en"]

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.legal_changed.value)
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_config_public_reflects_flags(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    await client.put(
        "/api/admin/settings/legal",
        json={
            "imprint": {"enabled": True, "en": "x", "de": ""},
            "privacy": {"enabled": True, "en": "y", "de": ""},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    legal = (await client.get("/api/config-public")).json()["legal"]
    assert legal["imprint_enabled"] is True
    assert legal["privacy_enabled"] is True


@pytest.mark.asyncio
async def test_unknown_kind_404(client):
    assert (await client.get("/api/legal/nope")).status_code == 404


@pytest.mark.asyncio
async def test_disabled_kind_reports_false(client):
    # Nothing configured yet -> disabled, empty html.
    body = (await client.get("/api/legal/privacy")).json()
    assert body["enabled"] is False
    assert body["html_en"] == ""


@pytest.mark.asyncio
async def test_admin_only(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    r = await client.get(
        "/api/admin/settings/legal", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
