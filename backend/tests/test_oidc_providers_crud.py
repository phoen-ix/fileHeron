"""Phase 10: admin CRUD endpoints for OIDC providers.

Covers: list, create, get, patch, delete, presets, test-discovery,
secret masking on responses, and refusal-to-delete-when-linked."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_list_providers_admin_only(make_user, client, login_as):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/sso/providers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_presets_endpoint_returns_known_presets(make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/sso/presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {p["preset"] for p in body["presets"]}
    assert {"entra", "google", "authentik", "keycloak", "custom"}.issubset(keys)
    # Google does not support groups → role mapping must skip it.
    google = next(p for p in body["presets"] if p["preset"] == "google")
    assert google["supports_groups"] is False


@pytest.mark.asyncio
async def test_create_get_patch_provider(make_user, client, login_as, db):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    create_resp = await client.post(
        "/api/admin/settings/sso/providers",
        json={
            "name": "Corp Entra",
            "preset": "entra",
            "issuer_url": "https://login.microsoftonline.com/corp/v2.0",
            "client_id": "corp-client",
            "client_secret": "corp-secret",
            "groups_claim": "groups",
            "admin_groups": "fh-admins",
            "employee_groups": "fh-employees",
            "redirect_uri": "",
            "enabled": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    pid = created["id"]
    # Secret never echoed back.
    assert "client_secret" not in created
    assert created["client_secret_set"] is True
    assert "corp-secret" not in create_resp.text

    # Audit row.
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_provider_created.value)
        .all()
    )
    assert len(rows) == 1

    # GET single.
    get_resp = await client.get(
        f"/api/admin/settings/sso/providers/{pid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["client_secret_set"] is True

    # PATCH name only - secret unchanged.
    patch_resp = await client.patch(
        f"/api/admin/settings/sso/providers/{pid}",
        json={"name": "Corp Entra (renamed)"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Corp Entra (renamed)"
    assert patch_resp.json()["client_secret_set"] is True


@pytest.mark.asyncio
async def test_patch_with_empty_secret_clears_it(make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    create = await client.post(
        "/api/admin/settings/sso/providers",
        json={
            "name": "X", "preset": "custom",
            "issuer_url": "https://x.example.com", "client_id": "cid",
            "client_secret": "shh",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    pid = create.json()["id"]
    patch = await client.patch(
        f"/api/admin/settings/sso/providers/{pid}",
        json={"client_secret": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch.status_code == 200
    assert patch.json()["client_secret_set"] is False


@pytest.mark.asyncio
async def test_delete_refuses_when_linked(
    make_user, make_provider, client, login_as, db
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    p = make_provider()
    bound = make_user(email="bound@test.local", role=UserRole.employee)
    bound.oidc_provider_id = p.id
    bound.oidc_subject = "bound-sub"
    db.commit()

    resp = await client.delete(
        f"/api/admin/settings/sso/providers/{p.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "OIDC_PROVIDER_HAS_USERS"
    assert body["details"]["linked_user_count"] == 1


@pytest.mark.asyncio
async def test_delete_succeeds_when_no_users_linked(
    make_user, make_provider, client, login_as, db
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    p = make_provider()
    resp = await client.delete(
        f"/api/admin/settings/sso/providers/{p.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_provider_deleted.value)
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_test_discovery_no_issuer_returns_error(make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/admin/settings/sso/test-discovery",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_config_public_lists_enabled_providers(
    make_user, make_provider, client
):
    make_provider(name="Visible", enabled=True)
    make_provider(name="Hidden", enabled=False)
    resp = await client.get("/api/config-public")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["providers"]]
    assert "Visible" in names
    assert "Hidden" not in names
