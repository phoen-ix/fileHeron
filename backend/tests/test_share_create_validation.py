"""POST /api/shares: recipient + public-link validation.

A share without recipients used to be flat-out refused. Now it's
allowed iff an inline public_link is attached - the public link IS
the access mechanism for an anonymous-link share.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import UserRole


def _expires_in(hours: int) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None)
        + timedelta(hours=hours)
    ).isoformat()


@pytest.mark.asyncio
async def test_create_refuses_when_no_recipient_and_no_public_link(
    make_user, db, client, login_as
):
    make_user(
        email="s@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("s@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [], "group_ids": []},
            "expires_at": _expires_in(24),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "recipient" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_allows_no_recipient_when_public_link_set(
    make_user, db, client, login_as
):
    make_user(
        email="s@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("s@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [], "group_ids": []},
            "expires_at": _expires_in(24),
            "subject": "anonymous link only",
            "public_link": {"notify_on_download": False},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["recipient_user_ids"] == []
    assert body["recipient_groups"] == []
    assert body["public_link"] is not None
    assert body["public_link"]["url"].startswith("http")


@pytest.mark.asyncio
async def test_create_share_rate_limited(make_user, client, login_as, monkeypatch):
    """audit #2: the per-sender share-creation rate limit returns 429 when
    exceeded (the limiter check runs first, before any recipient validation)."""
    from app.routers import shares as shares_mod

    make_user(email="emp@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    token, _ = await login_as("emp@test.local", "Pass12345678!")
    monkeypatch.setattr(shares_mod.rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [recipient.id], "group_ids": []},
            "expires_at": _expires_in(24),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_create_allows_recipients_only_unchanged(
    make_user, db, client, login_as
):
    """Sanity: the existing happy path (recipients, no public link)
    still works after the validator change."""
    make_user(
        email="s@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _expires_in(24),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["recipient_user_ids"] == [rec.id]
