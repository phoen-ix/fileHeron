"""A session ends N days after its sign-in, however active it stays (v2.25.2).

Every refresh minted a fresh REFRESH_TOKEN_EXPIRE_DAYS token, and the
notification bell refreshes every 15 minutes - so an open tab stayed signed in
forever (seen on the reference instance: one rotation per 15 minutes, around the
clock, for a sign-in days old). `auth.session_max_age_days` caps the chain at its
SIGN-IN, which rotation carries forward in `created_at`.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.refresh_token import RefreshToken
from app.services import settings as s
from app.utils.timeutil import utc_now

PW = "LongCorrectHorse123!"


async def _signed_in(make_user, client, db):
    user = make_user(email="alice@test.local", password=PW)
    resp = await client.post("/api/auth/login", json={"email": user.email, "password": PW})
    assert resp.status_code == 200, resp.text
    return user


def _chain(db, user):
    return (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id)
        .order_by(RefreshToken.id)
        .all()
    )


def _age_the_sign_in(db, user, days: float):
    for row in _chain(db, user):
        row.created_at = utc_now() - timedelta(days=days)
    db.commit()


@pytest.mark.asyncio
async def test_a_session_older_than_the_cap_must_sign_in_again(make_user, client, db):
    user = await _signed_in(make_user, client, db)
    s.set_value(db, key=s.Keys.SESSION_MAX_AGE_DAYS, value="30", actor=None)
    _age_the_sign_in(db, user, 30.01)

    resp = await client.post("/api/auth/refresh")

    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "INVALID_REFRESH"
    db.expire_all()
    rows = _chain(db, user)
    assert len(rows) == 1 and rows[0].revoked_at is not None  # retired, nothing minted
    assert rows[0].replaced_by_id is None  # soft: not a rotation, so not "reuse"
    audit = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.session_max_age_reached.value
    ).one()
    assert audit.actor_user_id == user.id and audit.extra["max_age_days"] == 30
    # Age is not theft: the user's OTHER sessions are untouched.
    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.refresh_token_reused.value
    ).count() == 0


@pytest.mark.asyncio
async def test_a_session_inside_the_cap_rotates_and_never_outlives_it(make_user, client, db):
    user = await _signed_in(make_user, client, db)
    s.set_value(db, key=s.Keys.SESSION_MAX_AGE_DAYS, value="30", actor=None)
    _age_the_sign_in(db, user, 28)  # two days left - less than the 7-day refresh TTL

    resp = await client.post("/api/auth/refresh")

    assert resp.status_code == 200, resp.text
    db.expire_all()
    newest = _chain(db, user)[-1]
    signed_in = newest.created_at
    assert newest.expires_at <= signed_in + timedelta(days=30)
    assert newest.expires_at < utc_now() + timedelta(days=7)  # clamped, not a fresh week


@pytest.mark.asyncio
async def test_zero_means_no_cap(make_user, client, db):
    user = await _signed_in(make_user, client, db)
    s.set_value(db, key=s.Keys.SESSION_MAX_AGE_DAYS, value="0", actor=None)
    _age_the_sign_in(db, user, 400)

    resp = await client.post("/api/auth/refresh")

    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert _chain(db, user)[-1].expires_at > utc_now() + timedelta(days=6)


def test_the_default_is_thirty_days(db):
    from app.services import settings_registry

    assert settings_registry.effective(db, s.Keys.SESSION_MAX_AGE_DAYS) == 30
