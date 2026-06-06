"""Desktop-client logo: /api/branding/logo.png gating + show_client toggle."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.models.user import UserRole

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _real_png(w=300, h=120) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "blue").save(out, "PNG")
    return out.getvalue()


async def _admin_token(make_user, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    return token


async def _upload(client, token, data=_PNG):
    r = await client.post(
        "/api/admin/settings/branding/logo",
        files={"file": ("logo.png", data, "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r


@pytest.mark.asyncio
async def test_404_when_nothing_uploaded(client):
    assert (await client.get("/api/branding/logo.png")).status_code == 404


@pytest.mark.asyncio
async def test_404_when_show_client_off_even_with_logo(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    await _upload(client, token, _real_png())
    # show_client defaults off -> 404.
    assert (await client.get("/api/branding/logo.png")).status_code == 404


@pytest.mark.asyncio
async def test_serves_png_when_enabled(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    await _upload(client, token, _real_png())
    r = await client.put(
        "/api/admin/settings/branding",
        json={"show_client": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["show_client"] is True

    served = await client.get("/api/branding/logo.png")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content.startswith(b"\x89PNG\r\n\x1a\n")
    # The rendition is downscaled to the header height.
    im = Image.open(io.BytesIO(served.content))
    assert im.height <= 48


@pytest.mark.asyncio
async def test_delete_clears_client_png(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    await _upload(client, token, _real_png())
    await client.put(
        "/api/admin/settings/branding",
        json={"show_client": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert (await client.get("/api/branding/logo.png")).status_code == 200
    await client.delete(
        "/api/admin/settings/branding/logo", headers={"Authorization": f"Bearer {token}"}
    )
    assert (await client.get("/api/branding/logo.png")).status_code == 404


@pytest.mark.asyncio
async def test_show_client_in_branding_response(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    r = await client.get(
        "/api/admin/settings/branding", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["show_client"] is False
