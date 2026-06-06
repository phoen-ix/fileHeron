"""PATCH /api/account/admin-nav-mode + /admin-nav-open — per-admin sidebar prefs."""
from __future__ import annotations

import pytest

from app.models.user import AdminNavCollapseMode, UserRole

_PW = "Pass12345678!"


async def _admin_token(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin, password=_PW)
    token, _ = await login_as(email, _PW)
    return token


@pytest.mark.asyncio
async def test_set_and_clear_mode(make_user, db, client, login_as):
    user = make_user(email="admin@test.local", role=UserRole.admin, password=_PW)
    token, _ = await login_as("admin@test.local", _PW)

    resp = await client.patch(
        "/api/account/admin-nav-mode",
        json={"mode": "manual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["admin_nav_collapse_mode"] == "manual"
    db.refresh(user)
    assert user.admin_nav_collapse_mode is AdminNavCollapseMode.manual

    resp_clear = await client.patch(
        "/api/account/admin-nav-mode",
        json={"mode": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_clear.status_code == 200
    assert resp_clear.json()["admin_nav_collapse_mode"] is None
    db.refresh(user)
    assert user.admin_nav_collapse_mode is None


@pytest.mark.asyncio
async def test_rejects_invalid_mode(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.patch(
        "/api/account/admin-nav-mode",
        json={"mode": "flippy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_ADMIN_NAV_MODE"


@pytest.mark.asyncio
async def test_set_open_categories(make_user, db, client, login_as):
    user = make_user(email="admin@test.local", role=UserRole.admin, password=_PW)
    token, _ = await login_as("admin@test.local", _PW)
    resp = await client.patch(
        "/api/account/admin-nav-open",
        json={"open": ["access", "system"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["admin_nav_open_categories"] == ["access", "system"]
    db.refresh(user)
    assert user.admin_nav_open_categories == ["access", "system"]


@pytest.mark.asyncio
async def test_open_categories_deduped_and_ordered(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.patch(
        "/api/account/admin-nav-open",
        json={"open": ["system", "access", "access"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    # Canonical order (access before system), de-duped.
    assert resp.json()["admin_nav_open_categories"] == ["access", "system"]


@pytest.mark.asyncio
async def test_rejects_invalid_category_key(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.patch(
        "/api/account/admin-nav-open",
        json={"open": ["access", "bogus"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_ADMIN_NAV_CATEGORY"
    assert body["details"]["invalid"] == ["bogus"]


@pytest.mark.asyncio
async def test_null_vs_empty_open_set(make_user, db, client, login_as):
    """NULL (never set) must be distinguishable from [] (explicit all-collapsed)."""
    user = make_user(email="admin@test.local", role=UserRole.admin, password=_PW)
    token, _ = await login_as("admin@test.local", _PW)

    # Fresh admin: never set → None.
    me = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.json()["admin_nav_open_categories"] is None

    resp = await client.patch(
        "/api/account/admin-nav-open",
        json={"open": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["admin_nav_open_categories"] == []
    db.refresh(user)
    assert user.admin_nav_open_categories == []


@pytest.mark.asyncio
async def test_mode_change_resets_open_set(make_user, db, client, login_as):
    user = make_user(email="admin@test.local", role=UserRole.admin, password=_PW)
    token, _ = await login_as("admin@test.local", _PW)

    await client.patch(
        "/api/account/admin-nav-open",
        json={"open": ["access", "system"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.patch(
        "/api/account/admin-nav-mode",
        json={"mode": "expanded"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["admin_nav_open_categories"] is None
    db.refresh(user)
    assert user.admin_nav_open_categories is None


@pytest.mark.asyncio
async def test_fields_surface_in_me(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {token}"}
    )
    body = resp.json()
    assert "admin_nav_collapse_mode" in body
    assert body["admin_nav_collapse_mode"] is None
    assert "admin_nav_open_categories" in body
    assert body["admin_nav_open_categories"] is None


@pytest.mark.asyncio
async def test_requires_admin(make_user, client, login_as):
    """Non-admins are gated out (get_current_admin → 403); no auth → 401."""
    make_user(email="client@test.local", role=UserRole.client, password=_PW)
    token, _ = await login_as("client@test.local", _PW)
    for path, body in (
        ("/api/account/admin-nav-mode", {"mode": "manual"}),
        ("/api/account/admin-nav-open", {"open": ["access"]}),
    ):
        resp = await client.patch(
            path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403, f"{path}: {resp.text}"
        assert resp.json()["code"] == "FORBIDDEN"

    no_auth = await client.patch(
        "/api/account/admin-nav-mode", json={"mode": "manual"}
    )
    assert no_auth.status_code == 401
