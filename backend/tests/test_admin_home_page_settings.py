"""GET/PUT /api/admin/settings/home-page (post-Phase 10)."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_get_default_is_enabled(make_user, client, login_as):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/home-page",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_put_disables_and_audits(
    make_user, db, client, login_as
):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/admin/settings/home-page",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.home_page_toggled.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["enabled"] is False


@pytest.mark.asyncio
async def test_me_response_reflects_admin_toggle(
    make_user, db, client, login_as
):
    """Disabling the home page in admin settings makes
    `MeResponse.home_page_enabled` flip for every user immediately."""
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    make_user(
        email="o@test.local", role=UserRole.client, password="Pass12345678!"
    )
    admin_token, _ = await login_as("a@test.local", "Pass12345678!")
    other_token, _ = await login_as("o@test.local", "Pass12345678!")

    await client.put(
        "/api/admin/settings/home-page",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    me_resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["home_page_enabled"] is False


@pytest.mark.asyncio
async def test_admin_only(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    for method in ("get", "put"):
        if method == "get":
            r = await client.get(
                "/api/admin/settings/home-page",
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            r = await client.put(
                "/api/admin/settings/home-page",
                json={"enabled": False},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403
