"""Inline public-link creation as part of POST /api/shares (post-Phase 10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.public_link import PublicLink
from app.models.user import UserRole
from app.services import settings as settings_svc


def _future_iso(days: int = 7) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    ).isoformat()


@pytest.mark.asyncio
async def test_create_share_with_inline_public_link(
    make_user, db, client, login_as
):
    make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "Q3",
            "message": None,
            "public_link": {
                "password": None,
                "download_limit": 5,
                "notify_on_download": True,
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["public_link"] is not None
    assert body["public_link"]["url"].startswith("http")
    assert body["public_link"]["download_limit"] == 5
    assert body["public_link"]["downloads_remaining"] == 5
    assert body["public_link"]["has_password"] is False
    # PublicLink row exists for the new share.
    pl = (
        db.query(PublicLink)
        .filter(PublicLink.share_id == body["id"])
        .one_or_none()
    )
    assert pl is not None
    assert pl.notify_on_download is True


@pytest.mark.asyncio
async def test_create_share_without_public_link_block(
    make_user, db, client, login_as
):
    """The optional block is truly optional - no link created."""
    make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "no-link",
            "message": None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body.get("public_link") is None
    assert (
        db.query(PublicLink)
        .filter(PublicLink.share_id == body["id"])
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_create_share_with_inline_link_password(
    make_user, db, client, login_as
):
    make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "secure",
            "message": None,
            "public_link": {"password": "shh-me", "notify_on_download": False},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["public_link"]["has_password"] is True


@pytest.mark.asyncio
async def test_create_share_inline_link_blocked_by_policy(
    make_user, db, client, login_as
):
    """A user under a restrictive policy gets 403 - and the share is NOT
    created (atomicity)."""
    employee = make_user(
        email="e@test.local",
        role=UserRole.employee,
        password="Pass12345678!",
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    # Create the connection so an outbound share is allowed in the first place.
    from app.services.connection import record_invite_connection

    record_invite_connection(db, inviter=employee, invitee=rec)
    db.commit()

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_POLICY_MODE,
        value="admins_only",
        actor=None,
    )
    db.commit()

    token, _ = await login_as("e@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "blocked",
            "message": None,
            "public_link": {"notify_on_download": False},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "PUBLIC_LINK_NOT_ALLOWED"

    # Atomicity: NO share row should have been created.
    from app.models.share import Share

    employee_shares = (
        db.query(Share).filter(Share.created_by_id == employee.id).count()
    )
    assert employee_shares == 0
