"""PATCH /api/account/default-landing-page + effective-landing helper."""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import account_prefs
from app.services import settings as settings_svc


@pytest.mark.asyncio
async def test_set_and_clear(make_user, db, client, login_as):
    user = make_user(
        email="lp@test.local", role=UserRole.client, password="Pass12345678!"
    )
    token, _ = await login_as("lp@test.local", "Pass12345678!")

    resp = await client.patch(
        "/api/account/default-landing-page",
        json={"default_landing_page": "outbox"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_landing_page"] == "outbox"
    db.refresh(user)
    assert user.default_landing_page == "outbox"

    resp_clear = await client.patch(
        "/api/account/default-landing-page",
        json={"default_landing_page": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_clear.status_code == 200
    assert resp_clear.json()["default_landing_page"] is None


@pytest.mark.asyncio
async def test_rejects_unknown_route(make_user, client, login_as):
    make_user(email="lp@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("lp@test.local", "Pass12345678!")
    resp = await client.patch(
        "/api/account/default-landing-page",
        json={"default_landing_page": "shenanigans"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_LANDING_PAGE"


@pytest.mark.asyncio
async def test_rejects_admin_route(make_user, client, login_as):
    """Admin pages aren't in ALLOWED_LANDING_ROUTES — even an admin
    can't pick them as their landing (per the design choice)."""
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.patch(
        "/api/account/default-landing-page",
        json={"default_landing_page": "admin-users"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_LANDING_PAGE"


@pytest.mark.asyncio
async def test_rejects_home_when_disabled(
    make_user, db, client, login_as
):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.HOME_PAGE_ENABLED,
        value="false",
        actor=admin,
    )
    db.commit()
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.patch(
        "/api/account/default-landing-page",
        json={"default_landing_page": "home"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "HOME_PAGE_DISABLED"


@pytest.mark.asyncio
async def test_me_response_carries_new_fields(
    make_user, db, client, login_as
):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {token}"}
    )
    body = resp.json()
    assert "default_landing_page" in body
    assert body["default_landing_page"] is None
    assert "home_page_enabled" in body
    assert body["home_page_enabled"] is True


def test_effective_landing_route_matrix(make_user, db):
    user = make_user(email="x@test.local", role=UserRole.client)

    # No pref + home enabled → "home".
    assert (
        account_prefs.effective_landing_route(user, home_enabled=True) == "home"
    )
    # No pref + home disabled → fallback "share-create".
    assert (
        account_prefs.effective_landing_route(user, home_enabled=False)
        == "share-create"
    )

    # Explicit pref honoured.
    user.default_landing_page = "inbox"
    assert (
        account_prefs.effective_landing_route(user, home_enabled=True) == "inbox"
    )
    assert (
        account_prefs.effective_landing_route(user, home_enabled=False) == "inbox"
    )

    # Pref = "home" with home disabled → fallback.
    user.default_landing_page = "home"
    assert (
        account_prefs.effective_landing_route(user, home_enabled=False)
        == "share-create"
    )
    assert (
        account_prefs.effective_landing_route(user, home_enabled=True) == "home"
    )

    # Stale / unknown pref → ignored, fall through.
    user.default_landing_page = "ghost-route"
    assert (
        account_prefs.effective_landing_route(user, home_enabled=True) == "home"
    )
