"""Admin session oversight (v1.7.0): GET /api/admin/sessions + revoke paths.

A session = a `refresh_tokens` row. Admins list every user's sessions, filter
by user, toggle expired/revoked in/out, sort by last activity, and revoke a
single session or all of one user's sessions — each audited.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import UserRole


def _utcnow():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed(db, user_id, *, hash_suffix, created_delta=0, revoked=False, expired=False):
    now = _utcnow()
    row = RefreshToken(
        user_id=user_id,
        token_hash=(hash_suffix * 64)[:64],
        created_at=now - timedelta(minutes=created_delta),
        last_used_at=now - timedelta(minutes=created_delta),
        expires_at=(now - timedelta(seconds=1)) if expired else (now + timedelta(days=7)),
        revoked_at=now if revoked else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_list_sessions_cross_user_with_hydration(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    alice = make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    _seed(db, alice.id, hash_suffix="a")
    _seed(db, admin.id, hash_suffix="b")

    resp = await client.get(
        "/api/admin/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    by_user = {i["user_id"]: i for i in data["items"]}
    assert alice.id in by_user
    assert by_user[alice.id]["user_email"] == "alice@test.local"
    assert by_user[alice.id]["user_display_name"] == "Test User"
    assert by_user[alice.id]["is_active"] is True


@pytest.mark.asyncio
async def test_filter_by_user_and_include_inactive(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    alice = make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    bob = make_user(email="bob@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    _seed(db, alice.id, hash_suffix="a")
    _seed(db, alice.id, hash_suffix="c", revoked=True)
    _seed(db, bob.id, hash_suffix="d")

    # Default: only Alice's active session.
    resp = await client.get(
        f"/api/admin/sessions?user_id={alice.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = resp.json()["items"]
    assert {i["user_id"] for i in items} == {alice.id}
    assert all(i["is_active"] for i in items)
    assert len(items) == 1

    # include_inactive surfaces the revoked one too.
    resp = await client.get(
        f"/api/admin/sessions?user_id={alice.id}&include_inactive=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = resp.json()["items"]
    assert len(items) == 2
    assert any(i["is_active"] is False for i in items)


@pytest.mark.asyncio
async def test_sort_by_last_used(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    alice = make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    old = _seed(db, alice.id, hash_suffix="a", created_delta=600)
    recent = _seed(db, alice.id, hash_suffix="c", created_delta=1)

    resp = await client.get(
        f"/api/admin/sessions?user_id={alice.id}&sort=last_used_at&direction=asc",
        headers={"Authorization": f"Bearer {token}"},
    )
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids.index(old.id) < ids.index(recent.id)


@pytest.mark.asyncio
async def test_revoke_one_session_audited(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    alice = make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    row = _seed(db, alice.id, hash_suffix="a")

    resp = await client.delete(
        f"/api/admin/sessions/{row.id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    db.refresh(row)
    assert row.revoked_at is not None

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.refresh_token_admin_revoked.value)
        .one()
    )
    assert audit.actor_user_id == admin.id
    assert audit.target_type == "refresh_token"

    # Unknown session → 404.
    resp = await client.delete(
        "/api/admin/sessions/999999", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_all_for_user_scoped(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    alice = make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    bob = make_user(email="bob@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    a1 = _seed(db, alice.id, hash_suffix="a")
    a2 = _seed(db, alice.id, hash_suffix="c")
    b1 = _seed(db, bob.id, hash_suffix="d")

    resp = await client.delete(
        f"/api/admin/users/{alice.id}/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] == 2

    for r in (a1, a2, b1):
        db.refresh(r)
    assert a1.revoked_at is not None and a2.revoked_at is not None
    assert b1.revoked_at is None  # bob untouched


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("alice@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
