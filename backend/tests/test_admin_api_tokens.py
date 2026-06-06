"""Admin inventory + on-behalf creation + disable/reactivate/revoke
(post-Phase 10).

Covers route-level behavior for the new `/api/admin/api-tokens/*` and
`/api/admin/settings/api-tokens/policy` endpoints.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import api_token as api_token_svc
from app.utils.timeutil import utc_now


@pytest.mark.asyncio
async def test_admin_can_list_all_tokens(make_user, db, client, login_as):
    admin = make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    api_token_svc.create_token(db, owner=bob, name="bob-ci")
    api_token_svc.create_token(db, owner=admin, name="admin-ci")
    db.commit()

    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/api-tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"bob-ci", "admin-ci"}
    # Each row carries owner info + status.
    for item in body["items"]:
        assert item["status"] == "active"
        assert "owner_display_name" in item


@pytest.mark.asyncio
async def test_admin_list_derives_expiry_status(make_user, db, client, login_as):
    """Listing tokens that carry an `expires_at` must derive active/expired
    without erroring (regression: `_token_status` called a non-existent
    `api_token_svc._utcnow`, 500ing on any token with an expiry set)."""
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    api_token_svc.create_token(
        db, owner=bob, name="past", expires_at=utc_now() - timedelta(hours=1)
    )
    api_token_svc.create_token(
        db, owner=bob, name="future", expires_at=utc_now() + timedelta(days=7)
    )
    db.commit()

    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/api-tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    status_by_name = {item["name"]: item["status"] for item in resp.json()["items"]}
    assert status_by_name == {"past": "expired", "future": "active"}


@pytest.mark.asyncio
async def test_admin_create_for_returns_plaintext_and_audits(
    make_user, db, client, login_as
):
    admin = make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    target = make_user(email="t@test.local", role=UserRole.client)
    token, _ = await login_as("a@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/admin/api-tokens",
        json={"target_user_id": target.id, "name": "t-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["plaintext_token"].startswith("fh_")
    assert body["owner_user_id"] == target.id

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.api_token_admin_created.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == admin.id
    assert rows[0].extra["owner_user_id"] == target.id


@pytest.mark.asyncio
async def test_admin_disable_reactivate_round_trip(
    make_user, db, client, login_as
):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    rec, plaintext = api_token_svc.create_token(db, owner=bob, name="bob")
    db.commit()

    token, _ = await login_as("a@test.local", "Pass12345678!")

    # Disable → verify_token now refuses with API_TOKEN_DISABLED.
    r1 = await client.post(
        f"/api/admin/api-tokens/{rec.id}/disable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "disabled"

    db.expire_all()
    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=plaintext)
    assert exc.value.code == "API_TOKEN_DISABLED"

    # Reactivate → verify_token works again.
    r2 = await client.post(
        f"/api/admin/api-tokens/{rec.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "active"

    db.expire_all()
    resolved = api_token_svc.verify_token(db, token_str=plaintext)
    assert resolved.id == rec.id


@pytest.mark.asyncio
async def test_admin_permanent_revoke_blocks_reactivate(
    make_user, db, client, login_as
):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    rec, _ = api_token_svc.create_token(db, owner=bob, name="bob")
    db.commit()
    token, _ = await login_as("a@test.local", "Pass12345678!")

    r = await client.delete(
        f"/api/admin/api-tokens/{rec.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    # Reactivation refused with TOKEN_REVOKED.
    r2 = await client.post(
        f"/api/admin/api-tokens/{rec.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "TOKEN_REVOKED"


@pytest.mark.asyncio
async def test_admin_disable_refuses_when_already_revoked(
    make_user, db, client, login_as
):
    admin = make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    rec, _ = api_token_svc.create_token(db, owner=bob, name="bob")
    api_token_svc.admin_revoke_token(db, actor=admin, token_id=rec.id)
    db.commit()
    token, _ = await login_as("a@test.local", "Pass12345678!")

    r = await client.post(
        f"/api/admin/api-tokens/{rec.id}/disable",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_policy_get_put_round_trip(make_user, db, client, login_as):
    make_user(
        email="a@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    bob = make_user(email="b@test.local", role=UserRole.client)
    token, _ = await login_as("a@test.local", "Pass12345678!")

    # GET initial → defaults
    r1 = await client.get(
        "/api/admin/settings/api-tokens/policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["mode"] == "everyone"
    assert r1.json()["allowed_user_ids"] == []

    # PUT a stricter policy
    r2 = await client.put(
        "/api/admin/settings/api-tokens/policy",
        json={
            "mode": "admins_only",
            "allowed_user_ids": [bob.id],
            "allowed_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["mode"] == "admins_only"
    assert body["allowed_user_ids"] == [bob.id]
    assert body["allowed_users"][0]["id"] == bob.id

    # Audit row written with counts only (no IDs leaked into the log).
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.api_policy_changed.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["mode"] == "admins_only"
    assert rows[0].extra["user_count"] == 1


@pytest.mark.asyncio
async def test_policy_put_rejects_unknown_user(make_user, client, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    r = await client.put(
        "/api/admin/settings/api-tokens/policy",
        json={
            "mode": "admins_only",
            "allowed_user_ids": [9999],
            "allowed_group_ids": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_admin_only_routes_refuse_non_admins(
    make_user, client, login_as
):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    for path in (
        "/api/admin/api-tokens",
        "/api/admin/settings/api-tokens/policy",
    ):
        r = await client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403
