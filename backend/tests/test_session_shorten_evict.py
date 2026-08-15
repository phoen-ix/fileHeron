"""Retroactive refresh-TTL shortening + cap-eviction notification +
sign-out-other-sessions.

- reclamp_refresh_expiry: shortening the TTL clamps existing sessions down
  and revokes only ones already expired under the new value.
- PUT /settings/advanced lowering REFRESH_TOKEN_EXPIRE_DAYS triggers it.
- The session cap dispatches a `session_evicted` notification.
- POST /api/auth/sessions/revoke-others keeps the current session.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services import settings as settings_svc


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _tok(db, user_id, *, hash_, created_days_ago, ttl_days, last_used_days_ago=None):
    created = _now() - timedelta(days=created_days_ago)
    t = RefreshToken(
        user_id=user_id,
        token_hash=hash_,
        expires_at=created + timedelta(days=ttl_days),
    )
    db.add(t)
    db.flush()
    t.created_at = created  # override the default=now
    # A never-rotated token's last activity IS its creation (both default to now
    # at mint); a rotated one advances last_used_at while created_at is carried
    # forward. reclamp uses last activity (max-idle), so model it faithfully.
    since = created_days_ago if last_used_days_ago is None else last_used_days_ago
    t.last_used_at = _now() - timedelta(days=since)
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


def test_reclamp_max_idle_keeps_actively_rotated_session(make_user, db):
    """Max-idle: a session started long ago but recently rotated (fresh
    last_used_at) is clamped, NOT revoked - the old created_at-anchored code would
    have revoked it as if it were idle."""
    from app.services import jwt_session

    user = make_user(email="u@test.local")
    # Session started 6d ago, rotated just now, currently valid for ~4 more days.
    t = _tok(db, user.id, hash_="h_rot", created_days_ago=6, ttl_days=10, last_used_days_ago=0)
    db.commit()

    res = jwt_session.reclamp_refresh_expiry(db, new_days=3)
    db.commit()
    db.refresh(t)

    assert t.revoked_at is None  # active session survives
    assert res["revoked"] == 0
    assert res["clamped"] == 1  # clamped to last_used + 3d, still in the future
    assert t.expires_at > _now()


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


@pytest.mark.asyncio
async def test_revoke_others_kills_the_other_devices_access_token(
    make_user, db, client, login_as
):
    """The defect: this stamped only refresh_tokens.revoked_at, so every other
    device kept working on its unexpired ACCESS token - 15 minutes by default
    and admin-raisable to 1440 - while the SPA promised "all other browsers
    will need to log in again". The admin-side revoke already behaved
    correctly, so the user-facing panic button was the odd one out."""
    make_user(email="ro2@test.local", role=UserRole.client, password="Pass12345678!")
    token1, _ = await login_as("ro2@test.local", "Pass12345678!")   # the device to evict

    # The mark is compared at SECOND granularity with a strict `<` - deliberate,
    # so that revoking and re-minting inside one request does not sign the
    # caller out. A test that logs in and revokes inside the same second
    # therefore passes for the wrong reason. Cross the boundary for real rather
    # than hand-writing sessions_invalidated_at: this test exists to prove the
    # ROUTE stamps it, and the audit's own finding was that the column's only
    # coverage hand-wrote it, so deleting the stamp left the suite green.
    await asyncio.sleep(1.1)

    token2, _ = await login_as("ro2@test.local", "Pass12345678!")   # the caller

    # Both work beforehand.
    assert (await client.get("/api/account/me",
                             headers={"Authorization": f"Bearer {token1}"})).status_code == 200

    r = await client.post(
        "/api/auth/sessions/revoke-others",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert r.status_code == 200, r.text

    evicted = await client.get("/api/account/me",
                               headers={"Authorization": f"Bearer {token1}"})
    assert evicted.status_code == 401, evicted.text
    assert evicted.json()["code"] == "SESSION_REVOKED"

    # And the route - not the test - is what set the mark.
    db.expire_all()
    revoked_user = db.query(User).filter(User.email == "ro2@test.local").one()
    assert revoked_user.sessions_invalidated_at is not None


@pytest.mark.asyncio
async def test_revoke_others_does_not_sign_the_caller_out(make_user, db, client, login_as):
    """sessions_invalidated_at is per-user, so it necessarily invalidates the
    caller's own token too. Without the re-mint the admin signs themselves out
    by pressing "sign out other devices" - which is why this follows the
    change_password precedent rather than just stamping the mark."""
    make_user(email="ro3@test.local", role=UserRole.client, password="Pass12345678!")
    await login_as("ro3@test.local", "Pass12345678!")
    token2, _ = await login_as("ro3@test.local", "Pass12345678!")

    r = await client.post(
        "/api/auth/sessions/revoke-others",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert r.status_code == 200, r.text
    # The refresh cookie was replaced, so the SPA can bootstrap a fresh access
    # token immediately rather than being bounced to /login.
    assert "fh_refresh" in r.headers.get("set-cookie", "")

    db.expire_all()
    active = (
        db.query(RefreshToken)
        .join(User, User.id == RefreshToken.user_id)
        .filter(User.email == "ro3@test.local", RefreshToken.revoked_at.is_(None))
        .count()
    )
    assert active == 1, "exactly the re-minted session survives"
