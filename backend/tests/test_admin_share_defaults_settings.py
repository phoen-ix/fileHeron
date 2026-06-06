"""GET/PUT /api/admin/settings/share-defaults - admin-controlled default
state for the per-share `notify_recipients` checkbox."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_get_default_is_true(make_user, client, login_as):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/share-defaults",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["notify_recipients_default"] is True


@pytest.mark.asyncio
async def test_put_disables_and_audits(
    make_user, db, client, login_as
):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/admin/settings/share-defaults",
        json={"notify_recipients_default": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["notify_recipients_default"] is False

    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type
            == AuditEventType.share_defaults_policy_changed.value
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["notify_recipients_default"] is False


@pytest.mark.asyncio
async def test_me_response_reflects_admin_toggle(
    make_user, db, client, login_as
):
    """Flipping the kv shows up in MeResponse on the next /me hit."""
    make_user(
        email="admin@test.local",
        role=UserRole.admin,
        password="Pass12345678!",
    )
    make_user(
        email="user@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    admin_token, _ = await login_as("admin@test.local", "Pass12345678!")
    user_token, _ = await login_as("user@test.local", "Pass12345678!")

    # Default (kv missing) → MeResponse reports True.
    me1 = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert me1.json()["share_notify_recipients_default"] is True

    await client.put(
        "/api/admin/settings/share-defaults",
        json={"notify_recipients_default": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    me2 = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert me2.json()["share_notify_recipients_default"] is False


@pytest.mark.asyncio
async def test_admin_only(make_user, client, login_as):
    make_user(
        email="c@test.local", role=UserRole.client, password="Pass12345678!"
    )
    token, _ = await login_as("c@test.local", "Pass12345678!")
    r_get = await client.get(
        "/api/admin/settings/share-defaults",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_get.status_code == 403
    r_put = await client.put(
        "/api/admin/settings/share-defaults",
        json={"notify_recipients_default": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r_put.status_code == 403
