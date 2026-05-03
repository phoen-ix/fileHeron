"""Audit log emits one row per privileged action."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import invite as invite_svc


@pytest.mark.asyncio
async def test_login_success_emits_audit(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    assert resp.status_code == 200

    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.event_type == AuditEventType.login_success.value).all()
    assert len(rows) == 1
    assert rows[0].actor_user_id is not None


@pytest.mark.asyncio
async def test_login_failure_emits_audit(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "Wrong!"},
    )
    assert resp.status_code == 401

    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.event_type == AuditEventType.login_failure.value).all()
    assert len(rows) == 1
    assert rows[0].extra is not None
    # Phase 1b distinguishes bad_password / bad_totp / bad_recovery / unknown_email.
    assert rows[0].extra.get("reason") == "bad_password"


@pytest.mark.asyncio
async def test_register_emits_user_registered_and_invite_consumed(make_user, client, db):
    inviter = make_user(email="hr@test.local", role=UserRole.admin)
    _r, plaintext = invite_svc.create_invite(
        db, email="newbie@test.local", target_role=UserRole.client, created_by=inviter
    )
    db.commit()

    resp = await client.post(
        "/api/auth/register-from-invite",
        json={"token": plaintext, "password": "NewbiePass123!", "display_name": "Newbie", "locale": "en"},
    )
    assert resp.status_code == 200

    db.expire_all()
    types = {row.event_type for row in db.query(AuditLog).all()}
    assert AuditEventType.user_registered.value in types
    assert AuditEventType.invite_consumed.value in types


@pytest.mark.asyncio
async def test_refresh_rotation_emits_audit(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    await client.post(
        "/api/auth/login",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!"},
    )
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200

    db.expire_all()
    rows = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.refresh_token_rotated.value
    ).all()
    assert len(rows) >= 1
