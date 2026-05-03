"""PATCH /api/account/locale: persists the user's preferred language."""
from __future__ import annotations

import pytest

from app.models.user import Locale, UserRole


@pytest.mark.asyncio
async def test_patch_locale_persists(make_user, db, client, login_as):
    user = make_user(
        email="lang@test.local",
        password="Pass12345678!",
        locale=Locale.en,
    )
    token, _ = await login_as("lang@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/locale",
        json={"locale": "de"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["locale"] == "de"

    db.refresh(user)
    assert user.locale == Locale.de


@pytest.mark.asyncio
async def test_patch_locale_requires_auth(client):
    resp = await client.patch("/api/account/locale", json={"locale": "de"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_patch_locale_rejects_unknown_value(make_user, client, login_as):
    make_user(email="bad@test.local", password="Pass12345678!")
    token, _ = await login_as("bad@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/locale",
        json={"locale": "fr"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_locale_round_trip_via_me(make_user, client, login_as):
    """After saving, GET /me reflects the new locale."""
    make_user(
        email="round@test.local",
        password="Pass12345678!",
        locale=Locale.en,
    )
    token, _ = await login_as("round@test.local", "Pass12345678!")

    await client.patch(
        "/api/account/locale",
        json={"locale": "de"},
        headers={"Authorization": f"Bearer {token}"},
    )

    me_resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["locale"] == "de"


@pytest.mark.asyncio
async def test_patch_locale_admin_role_unaffected(make_user, db, client, login_as):
    """Locale change must not mutate role or any other user fields."""
    admin = make_user(
        email="admin@test.local",
        role=UserRole.admin,
        password="Pass12345678!",
        locale=Locale.en,
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    await client.patch(
        "/api/account/locale",
        json={"locale": "de"},
        headers={"Authorization": f"Bearer {token}"},
    )
    db.refresh(admin)
    assert admin.role == UserRole.admin
    assert admin.locale == Locale.de
