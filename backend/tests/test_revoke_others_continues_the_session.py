"""Sign-out-others continues the caller's session; it never starts one
(audit 2026-11-19).

`POST /api/auth/sessions/revoke-others` re-mints the caller after revoking the
family. It used to mint a FRESH session for any bearer of an access token,
cookie or not, stamped "signed in just now": a leaked 15-minute access token
bought a refresh cookie, and pressing the button before day 30 restarted
SESSION_MAX_AGE_DAYS indefinitely. The re-mint now needs the caller's own live
refresh cookie and keeps that session's sign-in time and absolute end.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.refresh_token import RefreshToken
from app.models.user import UserRole


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_a_bare_access_token_gets_no_refresh_session(make_user, db, client, login_as):
    user = make_user(email="bare@test.local", role=UserRole.client, password="Pass12345678!")
    access, _ = await login_as("bare@test.local", "Pass12345678!")

    client.cookies.clear()  # the caller holds ONLY the access token
    r = await client.post(
        "/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 1
    assert "fh_refresh" not in r.cookies or not r.cookies["fh_refresh"]

    db.expire_all()
    live = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .count()
    )
    assert live == 0, "no session may be minted from an access token alone"


@pytest.mark.asyncio
async def test_the_remint_keeps_the_sign_in_time_and_its_absolute_end(
    make_user, db, client, login_as
):
    user = make_user(email="keep@test.local", role=UserRole.client, password="Pass12345678!")
    access, _ = await login_as("keep@test.local", "Pass12345678!")
    signed_in = _now() - timedelta(days=29)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update(
        {RefreshToken.created_at: signed_in}
    )
    db.commit()

    r = await client.post(
        "/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 200, r.text
    assert r.cookies.get("fh_refresh"), "the caller's own session continues"

    db.expire_all()
    live = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .one()
    )
    assert abs(live.created_at - signed_in) < timedelta(seconds=1), (
        "the 30-day session clock must not restart"
    )
    assert live.expires_at <= signed_in + timedelta(days=30) + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_a_session_past_its_absolute_end_is_not_continued(
    make_user, db, client, login_as
):
    user = make_user(email="old@test.local", role=UserRole.client, password="Pass12345678!")
    access, _ = await login_as("old@test.local", "Pass12345678!")
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update(
        {RefreshToken.created_at: _now() - timedelta(days=31)}
    )
    db.commit()

    r = await client.post(
        "/api/auth/sessions/revoke-others", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .count()
        == 0
    )
