"""GET /api/auth/sessions excludes expired-but-not-revoked tokens.

Before this fix, a token could sit in the user's "Active sessions" UI
for the full TTL even after expires_at had passed (only revoked_at was
filtered). Now both clauses apply.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.refresh_token import RefreshToken
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_list_sessions_excludes_expired(
    make_user, db, client, login_as
):
    user = make_user(
        email="x@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    token, _ = await login_as("x@test.local", "Pass12345678!")

    # Seed an expired-but-not-revoked token directly in the DB.
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    expired = RefreshToken(
        user_id=user.id,
        token_hash="z" * 64,
        expires_at=now - timedelta(seconds=1),
    )
    db.add(expired)
    db.commit()
    db.refresh(expired)

    resp = await client.get(
        "/api/auth/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    # The expired token must not be listed (filter A).
    assert expired.id not in ids
    # The just-logged-in real token IS listed.
    assert len(items) >= 1
