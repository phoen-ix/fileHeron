"""An admin can lift a login lockout now.

`users.locked_until` was cleared only by a successful login or a password reset
- and a locked account cannot log in - so an admin could only tell a locked-out
user to wait out the window. POST /api/admin/users/{id}/unlock clears it."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import rate_limit as rl

PW = "Pass12345678!"


def _lock(db, user):
    for _ in range(5):
        rl.record_failure(db, user=user)
    db.commit()
    assert rl.is_account_locked(user)


async def _admin(make_user, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return admin, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unlock_clears_the_lock_and_the_user_can_sign_in(db, make_user, login_as, client):
    admin, h = await _admin(make_user, login_as)
    user = make_user(email="locked@test.local", role=UserRole.employee, password=PW)
    _lock(db, user)

    r = await client.get(f"/api/admin/users/{user.id}", headers=h)
    assert r.json()["locked_until"] is not None

    r = await client.post(f"/api/admin/users/{user.id}/unlock", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["locked_until"] is None
    db.expire_all()
    db.refresh(user)
    assert user.locked_until is None and user.failed_login_count == 0

    (row,) = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.account_unlocked.value
    ).all()
    assert row.actor_user_id == admin.id and row.target_id == str(user.id)
    assert row.extra == {"failed_login_count": 5}

    r = await client.post("/api/auth/login", json={"email": "locked@test.local", "password": PW})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_an_account_that_is_not_locked_is_left_alone(db, make_user, login_as, client):
    _admin_user, h = await _admin(make_user, login_as)
    user = make_user(email="fine@test.local", role=UserRole.employee)
    r = await client.post(f"/api/admin/users/{user.id}/unlock", headers=h)
    assert r.status_code == 200 and r.json()["locked_until"] is None
    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.account_unlocked.value
    ).count() == 0


@pytest.mark.asyncio
async def test_a_lapsed_lock_is_not_reported_as_locked(db, make_user, login_as, client):
    from datetime import timedelta

    from app.utils.timeutil import utc_now

    _admin_user, h = await _admin(make_user, login_as)
    user = make_user(email="lapsed@test.local", role=UserRole.employee)
    user.locked_until = utc_now() - timedelta(minutes=1)
    db.commit()
    r = await client.get(f"/api/admin/users/{user.id}", headers=h)
    assert r.json()["locked_until"] is None
    listed = (await client.get("/api/admin/users", headers=h)).json()["items"]
    assert next(u for u in listed if u["id"] == user.id)["locked_until"] is None


@pytest.mark.asyncio
async def test_the_list_shows_a_live_lock(db, make_user, login_as, client):
    _admin_user, h = await _admin(make_user, login_as)
    user = make_user(email="locked@test.local", role=UserRole.employee)
    _lock(db, user)
    listed = (await client.get("/api/admin/users", headers=h)).json()["items"]
    assert next(u for u in listed if u["id"] == user.id)["locked_until"] is not None


@pytest.mark.asyncio
async def test_only_an_admin_can_unlock(db, make_user, login_as, client):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    user = make_user(email="locked@test.local", role=UserRole.employee)
    _lock(db, user)
    token, _ = await login_as("emp@test.local", PW)
    r = await client.post(
        f"/api/admin/users/{user.id}/unlock", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    db.refresh(user)
    assert rl.is_account_locked(user)


@pytest.mark.asyncio
async def test_an_unknown_user_is_404(make_user, login_as, client):
    _admin_user, h = await _admin(make_user, login_as)
    r = await client.post("/api/admin/users/99999/unlock", headers=h)
    assert r.status_code == 404
