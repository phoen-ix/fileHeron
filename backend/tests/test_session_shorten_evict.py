"""Retroactive refresh-TTL shortening + cap-eviction notification +
sign-out-other-sessions.

- reclamp_refresh_expiry: shortening the TTL clamps existing sessions down
  and revokes only ones already expired under the new value.
- PUT /settings/advanced lowering REFRESH_TOKEN_EXPIRE_DAYS triggers it.
- The session cap dispatches a `session_evicted` notification.
- POST /api/auth/sessions/revoke-others keeps the current session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.refresh_token import RefreshToken
from app.models.user import UserRole
from app.services import settings as settings_svc


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _tok(db, user_id, *, hash_, created_days_ago, ttl_days):
    created = _now() - timedelta(days=created_days_ago)
    t = RefreshToken(
        user_id=user_id,
        token_hash=hash_,
        expires_at=created + timedelta(days=ttl_days),
    )
    db.add(t)
    db.flush()
    t.created_at = created  # override the default=now
    db.flush()
    return t


# ---- reclamp service -------------------------------------------------------


def test_reclamp_revokes_idle_clamps_recent(make_user, db):
    from app.services import jwt_session

    user = make_user(email="u@test.local")
    # Idle: created 5d ago at 7d TTL. created+3d = 2d ago < now → revoke.
    idle = _tok(db, user.id, hash_="h_idle", created_days_ago=5, ttl_days=7)
    # Recent: created today at 7d TTL. created+3d > now → clamp, keep.
    recent = _tok(db, user.id, hash_="h_recent", created_days_ago=0, ttl_days=7)
    db.commit()

    res = jwt_session.reclamp_refresh_expiry(db, new_days=3)
    db.commit()
    db.refresh(idle)
    db.refresh(recent)

    assert res["revoked"] >= 1
    assert idle.revoked_at is not None
    assert recent.revoked_at is None
    # recent clamped down to created+3d
    assert abs((recent.expires_at - (recent.created_at + timedelta(days=3))).total_seconds()) < 2


def test_reclamp_noop_when_not_shorter(make_user, db):
    from app.services import jwt_session

    user = make_user(email="u@test.local")
    t = _tok(db, user.id, hash_="h1", created_days_ago=0, ttl_days=7)
    db.commit()
    before = t.expires_at

    res = jwt_session.reclamp_refresh_expiry(db, new_days=10)  # longer → no change
    db.commit()
    db.refresh(t)
    assert res["clamped"] == 0 and res["revoked"] == 0
    assert t.expires_at == before


# ---- settings PUT triggers the clamp --------------------------------------


@pytest.mark.asyncio
async def test_settings_lowering_refresh_ttl_reclamps(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    idle = _tok(db, admin.id, hash_="h_idle2", created_days_ago=5, ttl_days=7)
    db.commit()

    token, _ = await login_as("admin@test.local", "TestPassword123!")
    r = await client.put(
        "/api/admin/settings/advanced",
        headers={"Authorization": f"Bearer {token}"},
        json={"updates": {settings_svc.Keys.REFRESH_TOKEN_EXPIRE_DAYS: 3}},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    db.refresh(idle)
    assert idle.revoked_at is not None  # idle session past the new 3d window → revoked


# ---- cap eviction notifies the user ---------------------------------------


@pytest.mark.asyncio
async def test_cap_eviction_dispatches_notification(make_user, db, client, login_as, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_ACTIVE_SESSIONS_PER_USER", 2)
    user = make_user(email="ev@test.local", role=UserRole.client, password="Pass12345678!")
    for _ in range(3):  # 3rd login over a cap of 2 → evicts the oldest
        await login_as("ev@test.local", "Pass12345678!")
    db.expire_all()
    n = (
        db.query(Notification)
        .filter(
            Notification.user_id == user.id,
            Notification.category == NotificationCategory.session_evicted,
        )
        .count()
    )
    assert n >= 1


# ---- sign out all other sessions ------------------------------------------


@pytest.mark.asyncio
async def test_revoke_others_keeps_current(make_user, db, client, login_as):
    user = make_user(email="ro@test.local", role=UserRole.client, password="Pass12345678!")
    await login_as("ro@test.local", "Pass12345678!")  # session 1
    token2, _ = await login_as("ro@test.local", "Pass12345678!")  # session 2 (current cookie in jar)

    r = await client.post(
        "/api/auth/sessions/revoke-others",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] >= 1

    db.expire_all()
    active = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .count()
    )
    assert active == 1  # only the current session survives
