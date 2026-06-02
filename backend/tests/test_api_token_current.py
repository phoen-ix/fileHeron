"""GET /api/account/api-tokens/current — returns metadata for the API token
authenticating the request, so a client can show which token it's running on.

JWT/session auth has no token → 400 NOT_API_TOKEN.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import api_token as api_token_svc


@pytest.mark.asyncio
async def test_current_returns_token_metadata(make_user, db, client):
    user = make_user(email="u@test.local", role=UserRole.employee)
    record, plaintext = api_token_svc.create_token(
        db, owner=user, name="desktop-client"
    )
    db.commit()

    resp = await client.get(
        "/api/account/api-tokens/current",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == record.id
    assert body["name"] == "desktop-client"
    assert body["last4"] == plaintext[-4:]
    assert body["status"] == "active"
    # Using the token updated last_used_at, so it should be set.
    assert body["last_used_at"] is not None


@pytest.mark.asyncio
async def test_current_rejects_jwt_session(make_user, db, client, login_as):
    make_user(
        email="u@test.local", role=UserRole.client, password="Pass12345678!"
    )
    token, _cookies = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/account/api-tokens/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "NOT_API_TOKEN"


@pytest.mark.asyncio
async def test_current_rejected_after_revoke(make_user, db, client):
    """Once revoked, the token can't even authenticate → 401 (verify_token),
    so /current is unreachable with it. Confirms revoke is immediate."""
    user = make_user(email="u@test.local", role=UserRole.employee)
    record, plaintext = api_token_svc.create_token(db, owner=user, name="t")
    api_token_svc.revoke_token(db, owner=user, token_id=record.id)
    db.commit()

    resp = await client.get(
        "/api/account/api-tokens/current",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 401, resp.text
