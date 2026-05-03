"""PATCH /api/account/display-name: self-service display name updates."""
from __future__ import annotations

import pytest

from app.models.user import UserRole


@pytest.mark.asyncio
async def test_patch_display_name_persists(make_user, db, client, login_as):
    user = make_user(
        email="rename@test.local",
        password="Pass12345678!",
        display_name="Old Name",
    )
    token, _ = await login_as("rename@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/display-name",
        json={"display_name": "Brand New Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Brand New Name"

    db.refresh(user)
    assert user.display_name == "Brand New Name"


@pytest.mark.asyncio
async def test_patch_display_name_requires_auth(client):
    resp = await client.patch(
        "/api/account/display-name",
        json={"display_name": "Anything"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_patch_display_name_rejects_empty(make_user, client, login_as):
    make_user(email="empty@test.local", password="Pass12345678!")
    token, _ = await login_as("empty@test.local", "Pass12345678!")

    # Pure whitespace passes the pydantic min_length but the route
    # trims and re-validates → 422 INVALID_DISPLAY_NAME.
    resp = await client.patch(
        "/api/account/display-name",
        json={"display_name": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_DISPLAY_NAME"

    # Truly empty string fails pydantic Field's min_length=1.
    resp2 = await client.patch(
        "/api/account/display-name",
        json={"display_name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_patch_display_name_rejects_too_long(make_user, client, login_as):
    make_user(email="long@test.local", password="Pass12345678!")
    token, _ = await login_as("long@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/display-name",
        json={"display_name": "x" * 121},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_display_name_round_trip_via_me(make_user, client, login_as):
    make_user(
        email="round@test.local",
        password="Pass12345678!",
        display_name="Initial",
    )
    token, _ = await login_as("round@test.local", "Pass12345678!")

    await client.patch(
        "/api/account/display-name",
        json={"display_name": "Renamed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    me_resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["display_name"] == "Renamed"


@pytest.mark.asyncio
async def test_patch_display_name_does_not_mutate_other_fields(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local",
        role=UserRole.admin,
        password="Pass12345678!",
        display_name="Admin",
    )
    original_role = admin.role
    original_email_hash = admin.email
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    await client.patch(
        "/api/account/display-name",
        json={"display_name": "Renamed Admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    db.refresh(admin)
    assert admin.role == original_role
    assert admin.email == original_email_hash
    assert admin.display_name == "Renamed Admin"


@pytest.mark.asyncio
async def test_patch_display_name_trims_whitespace(make_user, db, client, login_as):
    user = make_user(
        email="trim@test.local",
        password="Pass12345678!",
        display_name="Old",
    )
    token, _ = await login_as("trim@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/display-name",
        json={"display_name": "   Padded Name   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    db.refresh(user)
    assert user.display_name == "Padded Name"
