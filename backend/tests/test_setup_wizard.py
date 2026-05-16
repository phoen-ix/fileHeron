"""v1.0.0 first-admin setup wizard: anonymous /setup/status + one-shot
/setup/admin. Lockout after first admin exists."""
from __future__ import annotations

import pytest

from app.models.user import UserRole


@pytest.mark.asyncio
async def test_status_required_on_empty_db(client):
    r = await client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["required"] is True


@pytest.mark.asyncio
async def test_complete_setup_creates_admin(client, db):
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first.admin@test.local",
            "password": "AdminPassword123!",
            "display_name": "First Admin",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "first.admin@test.local"
    assert body["user_id"]

    from app.models.user import User
    db.expire_all()
    user = db.query(User).filter(User.email == "first.admin@test.local").one()
    assert user.role == UserRole.admin
    assert user.email_verified is True
    assert user.is_disabled is False


@pytest.mark.asyncio
async def test_status_flips_to_false_after_first_admin(client, db, make_user):
    make_user(email="adm@test.local", role=UserRole.admin)
    r = await client.get("/api/setup/status")
    assert r.json()["required"] is False


@pytest.mark.asyncio
async def test_second_setup_call_is_locked(client, db, make_user):
    make_user(email="adm@test.local", role=UserRole.admin)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "second.admin@test.local",
            "password": "AdminPassword123!",
            "display_name": "Second",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "SETUP_ALREADY_COMPLETE"


@pytest.mark.asyncio
async def test_weak_password_rejected(client, db):
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first.admin@test.local",
            "password": "short",
            "display_name": "First",
        },
    )
    assert r.status_code == 422  # Pydantic min_length=8


@pytest.mark.asyncio
async def test_duplicate_email_rejected(client, db, make_user):
    make_user(email="taken@test.local", role=UserRole.client)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "taken@test.local",
            "password": "AdminPassword123!",
            "display_name": "Conflict",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "EMAIL_TAKEN"
