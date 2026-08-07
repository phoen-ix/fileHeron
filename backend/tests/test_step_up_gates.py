"""Re-auth on the destructive / secret-revealing admin surfaces.

An admin access token alone used to be enough to export every secret the
installation holds, to replace the whole configuration, and to erase an account
irreversibly - while the *recoverable* self-update routes had re-prompted for
the password since they shipped. The gate was not absent by design, it was
inconsistent, so a stolen admin session bought strictly more than an admin
normally spends without re-typing their password.

These tests pin the gate on all three, because a re-auth check is exactly the
kind of thing that gets dropped in a refactor without anything going red.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"
WRONG = "not-the-password"


async def _admin(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin, password=PW)
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_backup_export_refuses_without_the_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/export",
        json={"categories": ["settings_branding"], "secret_mode": "exclude"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_backup_export_refuses_a_wrong_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/export",
        json={
            "categories": ["settings_branding"],
            "secret_mode": "exclude",
            "password": WRONG,
        },
        headers=headers,
    )
    # 403, not 401: the admin IS authenticated. A 401 would trip the SPA's
    # token-refresh interceptor, which would silently retry with the same wrong
    # password and show the user nothing at all.
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"


@pytest.mark.asyncio
async def test_erase_refuses_a_wrong_password(make_user, db, client, login_as):
    headers = await _admin(make_user, login_as)
    target = make_user(email="victim@test.local", role=UserRole.client, password=PW)
    db.commit()
    resp = await client.post(
        f"/api/admin/users/{target.id}/erase",
        json={"password": WRONG},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"
    # And the account is untouched - the gate runs before anything irreversible.
    db.refresh(target)
    assert not target.email.endswith("@erased.invalid")


@pytest.mark.asyncio
async def test_erase_refuses_without_a_body(make_user, db, client, login_as):
    headers = await _admin(make_user, login_as)
    target = make_user(email="victim@test.local", role=UserRole.client, password=PW)
    db.commit()
    resp = await client.post(
        f"/api/admin/users/{target.id}/erase", headers=headers
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_backup_import_refuses_a_wrong_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/import",
        files={"file": ("b.fhbackup.json", b"{}", "application/json")},
        data={"confirm": "true", "password": WRONG},
        headers=headers,
    )
    # Refused on the password BEFORE the artifact is parsed or applied.
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"
