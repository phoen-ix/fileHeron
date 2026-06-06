"""Regression: GET /api/account/me must accept API tokens.

The desktop client uses ``/api/account/me`` right after sign-in to
populate the title bar (display name + role). When the user signs in
with an API token, the call MUST work - otherwise the client gets
401 INVALID_TOKEN and bounces back to the login screen.

Bug: /me used ``get_current_user`` (JWT-only). Fixed by switching to
``get_actor`` (JWT or API token).
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import api_token as api_token_svc


@pytest.mark.asyncio
async def test_me_works_with_api_token(make_user, db, client):
    user = make_user(email="u@test.local", role=UserRole.employee)
    _record, plaintext = api_token_svc.create_token(
        db, owner=user, name="desktop-client"
    )
    db.commit()

    resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == user.id
    assert body["display_name"] == user.display_name
    assert body["role"] == "employee"


@pytest.mark.asyncio
async def test_me_still_works_with_jwt(make_user, db, client, login_as):
    """Sanity: switching to get_actor must not break the existing JWT path."""
    user = make_user(
        email="u@test.local", role=UserRole.client, password="Pass12345678!"
    )
    token, _cookies = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == user.id
