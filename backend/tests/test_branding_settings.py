"""Branding settings: logo upload/serve/delete + surfaces + link + config-public."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
_NOT_IMAGE = b"%PDF-1.4 not an image" + b"\x00" * 64


async def _admin_token(make_user, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    return token


@pytest.mark.asyncio
async def test_get_defaults(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.get(
        "/api/admin/settings/branding", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["logo"]["present"] is False
    assert body["show_header"] is False
    assert body["link_url"] is None


@pytest.mark.asyncio
async def test_put_surfaces_and_link_audits(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/branding",
        json={"show_header": True, "show_email": True, "link_url": "https://acme.example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["show_header"] is True
    assert body["show_email"] is True
    assert body["link_url"] == "https://acme.example"
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.branding_changed.value)
        .all()
    )
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_put_rejects_bad_link(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        "/api/admin/settings/branding",
        json={"link_url": "javascript:alert(1)"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("data,ctype", [(_PNG, "image/png"), (_JPEG, "image/jpeg"), (_WEBP, "image/webp")])
async def test_upload_serve_delete(make_user, client, login_as, data, ctype):
    token = await _admin_token(make_user, login_as)
    up = await client.post(
        "/api/admin/settings/branding/logo",
        files={"file": ("logo.bin", data, "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert up.status_code == 201, up.text
    assert up.json()["logo"]["present"] is True
    assert up.json()["logo"]["content_type"] == ctype

    # Anonymous serve returns the bytes.
    served = await client.get("/api/branding/logo")
    assert served.status_code == 200
    assert served.content == data

    # config-public now advertises the logo.
    cfg = await client.get("/api/config-public")
    assert cfg.json()["branding"]["logo_url"] == "/api/branding/logo"

    # Delete clears it.
    rem = await client.delete(
        "/api/admin/settings/branding/logo", headers={"Authorization": f"Bearer {token}"}
    )
    assert rem.status_code == 200
    assert rem.json()["logo"]["present"] is False
    assert (await client.get("/api/branding/logo")).status_code == 404


@pytest.mark.asyncio
async def test_upload_rejects_non_image(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.post(
        "/api/admin/settings/branding/logo",
        files={"file": ("x.png", _NOT_IMAGE, "image/png")},  # lies about type
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 415
    assert resp.json()["code"] == "INVALID_LOGO_TYPE"


@pytest.mark.asyncio
async def test_logo_404_when_unset(client):
    assert (await client.get("/api/branding/logo")).status_code == 404


@pytest.mark.asyncio
async def test_admin_only(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    r = await client.get(
        "/api/admin/settings/branding", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
