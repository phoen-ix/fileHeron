"""End-to-end auth flow tests against the real ASGI app via httpx.AsyncClient."""
from __future__ import annotations

import pytest

from app.models.refresh_token import RefreshToken
from app.models.user import UserRole
from app.services import invite as invite_svc


@pytest.mark.asyncio
async def test_login_succeeds_with_correct_password(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["expires_in_seconds"] > 0
    assert "fh_refresh" in resp.cookies


@pytest.mark.asyncio
async def test_login_fails_with_wrong_password(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "WrongPassword999!"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_fails_for_unknown_email(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "ghost@test.local", "password": "WhateverPassword12!"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_blocked_for_disabled_user(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!", is_disabled=True)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_refresh_rotates_and_returns_new_access_token(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")

    login = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    first_access = login.json()["access_token"]
    cookies1 = dict(login.cookies)

    # Reuse client cookies (httpx keeps them by default), call refresh
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200, refresh.text
    new_access = refresh.json()["access_token"]
    cookies2 = dict(refresh.cookies)

    assert new_access != first_access
    # The cookie should also have rotated.
    assert cookies1.get("fh_refresh") != cookies2.get("fh_refresh")


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_entire_family(make_user, client, db):
    user = make_user(email="alice@test.local", password="LongCorrectHorse123!")

    # Login + capture the original refresh cookie.
    login = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    original_refresh = login.cookies["fh_refresh"]

    # Use it once → rotated. The httpx client now holds the new cookie.
    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200

    # Replay the ORIGINAL refresh token → reuse detection must fire.
    # The jar now holds the rotated cookie; clear it and send the original
    # explicitly via the Cookie header so the replayed value is deterministic.
    # (httpx 0.28's cookie-jar dedup otherwise lets the rotated cookie — same
    # name, different domain — shadow a re-set original, so the server sees a
    # still-valid token and returns 200 instead of detecting reuse.)
    client.cookies.clear()
    replay = await client.post(
        "/api/auth/refresh",
        headers={"Cookie": f"fh_refresh={original_refresh}"},
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "TOKEN_REUSE"

    # All refresh tokens for this user should now be revoked.
    db.expire_all()
    tokens = db.query(RefreshToken).filter(RefreshToken.user_id == user.id).all()
    assert tokens, "expected at least one refresh token"
    assert all(t.revoked_at is not None for t in tokens), "expected all tokens revoked after reuse"


@pytest.mark.asyncio
async def test_logout_revokes_refresh(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204

    # The refresh cookie was cleared by the logout response's
    # Set-Cookie header (Max-Age=0); httpx applies that to its jar
    # automatically, so a bare request is enough to prove the
    # missing-cookie 401.
    nofresh = await client.post("/api/auth/refresh")
    assert nofresh.status_code == 401


@pytest.mark.asyncio
async def test_register_from_invite_creates_verified_user(make_user, client, db):
    inviter = make_user(email="hr@test.local", password="HRTestPassword123!", role=UserRole.admin)
    _record, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    db.commit()

    resp = await client.post(
        "/api/auth/register-from-invite",
        json={
            "token": plaintext,
            "password": "NewbiePassword123!",
            "display_name": "Newbie",
            "locale": "en",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]

    # Subsequent login works (verified, not disabled).
    login = await client.post(
        "/api/auth/login",
        json={"email": "newbie@test.local", "password": "NewbiePassword123!"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_register_from_invite_rejects_used_token(make_user, client, db):
    inviter = make_user(email="hr@test.local", password="HRTestPassword123!", role=UserRole.admin)
    _record, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    db.commit()

    first = await client.post(
        "/api/auth/register-from-invite",
        json={
            "token": plaintext,
            "password": "NewbiePassword123!",
            "display_name": "Newbie",
            "locale": "en",
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/register-from-invite",
        json={
            "token": plaintext,
            "password": "OtherPassword456!",
            "display_name": "Imposter",
            "locale": "en",
        },
    )
    assert second.status_code in (404, 410)
    assert second.json()["code"] in {"INVITE_USED", "INVITE_INVALID"}


@pytest.mark.asyncio
async def test_register_from_invite_rejects_breached_password(make_user, client, db, monkeypatch):
    """A new user's first password is also screened against HIBP: a
    valid-length but breached password is refused (422 PASSWORD_BREACHED)
    and the invite is left unconsumed."""
    from app.services import hibp as hibp_svc

    async def _breached(_pw, _db=None):
        return True

    monkeypatch.setattr(hibp_svc, "is_password_breached", _breached)

    inviter = make_user(email="hr@test.local", password="HRTestPassword123!", role=UserRole.admin)
    _record, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    db.commit()

    resp = await client.post(
        "/api/auth/register-from-invite",
        json={
            "token": plaintext,
            "password": "BreachedPassword123!",
            "display_name": "Newbie",
            "locale": "en",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "PASSWORD_BREACHED"


@pytest.mark.asyncio
async def test_forgot_then_reset_password(make_user, client, db):
    user = make_user(email="alice@test.local", password="OldPassword12345!")

    # Trigger reset (always 200, never reveals existence).
    resp = await client.post("/api/auth/forgot-password", json={"email": "alice@test.local"})
    assert resp.status_code == 200

    # The plaintext is in the most recent password_reset_tokens row's token_hash —
    # but we only stored the hash. For testing, generate a token manually via the service.
    # (In the real flow the token is delivered via email.)
    from app.services.auth import begin_password_reset

    result = begin_password_reset(db, email="alice@test.local", request=None)
    assert result is not None
    _u, plaintext = result
    db.commit()

    new_pw = "BrandNewSecure567!"
    reset = await client.post(
        "/api/auth/reset-password", json={"token": plaintext, "new_password": new_pw}
    )
    assert reset.status_code == 200

    # Old password no longer works
    bad = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "OldPassword12345!"},
    )
    assert bad.status_code == 401

    # New password does
    ok = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": new_pw},
    )
    assert ok.status_code == 200
    _ = user  # silence unused
