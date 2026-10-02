"""GET /api/account/email shows the caller's own live pending change.

Nothing exposed a pending change: it was invisible on the account page until
its token expired 24h later, and DELETE /api/account/email (the self-cancel)
had no caller. The page now shows it with a Cancel button; this is its read."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.email_change_token import EmailChangeToken
from app.models.user import UserRole
from app.services import email_change as email_change_svc
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


async def _signed_in(make_user, login_as, email="me@test.local"):
    user = make_user(email=email, role=UserRole.employee, password=PW)
    token, _ = await login_as(email, PW)
    return user, {"Authorization": f"Bearer {token}"}


def _request(db, user, new_email="new@test.local"):
    email_change_svc.request_email_change(
        db, target=user, new_email=new_email, initiated_by=user,
        request=None, skip_verification=False,
    )
    db.commit()


@pytest.mark.asyncio
async def test_nothing_pending(make_user, login_as, client):
    _user, h = await _signed_in(make_user, login_as)
    r = await client.get("/api/account/email", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"pending": None}


@pytest.mark.asyncio
async def test_a_pending_change_is_shown_then_cancelled(db, make_user, login_as, client):
    user, h = await _signed_in(make_user, login_as)
    _request(db, user)
    r = await client.get("/api/account/email", headers=h)
    body = r.json()["pending"]
    assert body["new_email"] == "new@test.local"
    row = db.query(EmailChangeToken).one()
    assert body["expires_at"] == row.expires_at.isoformat()

    r = await client.delete("/api/account/email", headers=h)
    assert r.json() == {"ok": True, "cancelled": 1}
    assert (await client.get("/api/account/email", headers=h)).json() == {"pending": None}


@pytest.mark.asyncio
async def test_an_expired_change_is_not_pending(db, make_user, login_as, client):
    user, h = await _signed_in(make_user, login_as)
    _request(db, user)
    row = db.query(EmailChangeToken).one()
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    assert (await client.get("/api/account/email", headers=h)).json() == {"pending": None}


@pytest.mark.asyncio
async def test_only_my_own_change_is_shown(db, make_user, login_as, client):
    other = make_user(email="other@test.local", role=UserRole.employee)
    _request(db, other, new_email="others-new@test.local")
    _user, h = await _signed_in(make_user, login_as)
    assert (await client.get("/api/account/email", headers=h)).json() == {"pending": None}


@pytest.mark.asyncio
async def test_the_newest_of_two_requests_is_shown(db, make_user, login_as, client):
    user, h = await _signed_in(make_user, login_as)
    _request(db, user, new_email="first@test.local")
    _request(db, user, new_email="second@test.local")
    assert (await client.get("/api/account/email", headers=h)).json()["pending"]["new_email"] == (
        "second@test.local"
    )
