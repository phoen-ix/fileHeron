"""Wave 1 auth + access-control hardening regressions (audit H1, M17, L30).

- H1: /api/auth/webauthn/begin must run the SAME pre-second-factor gate as
  /api/auth/login (per-IP throttle, account lockout, attempt recording). It
  previously ran a bare password check with none of those, making it an
  unthrottled password + enumeration oracle.
- M17: the last enabled admin cannot be demoted/disabled (org lockout).
- L30: a pre-minted API token stops working while its owner is locked out.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.middleware.errors import AppError
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.models.user import UserRole
from app.services import api_token as api_token_svc
from app.services import user_management as um_svc
from app.utils.timeutil import utc_now


@pytest.mark.asyncio
async def test_webauthn_begin_enforces_lockout_and_records_attempts(make_user, db, client):
    make_user(email="pk@test.local", password="CorrectHorse9!")

    saw_locked = False
    for _ in range(12):
        resp = await client.post(
            "/api/auth/webauthn/begin",
            json={"email": "pk@test.local", "password": "wrong-guess"},
        )
        if resp.status_code == 423:
            saw_locked = True
            assert resp.json()["code"] == "ACCOUNT_LOCKED"
            break
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "INVALID_CREDENTIALS"

    assert saw_locked, "account never locked - the lockout gate is not applied on /webauthn/begin"

    # The bad attempts are now visible to the forensics pipeline (previously zero).
    bad = (
        db.query(LoginAttempt)
        .filter(LoginAttempt.outcome == LoginOutcome.bad_password.value)
        .count()
    )
    assert bad >= 1


@pytest.mark.asyncio
async def test_webauthn_begin_unknown_email_is_uniform_401(client):
    resp = await client.post(
        "/api/auth/webauthn/begin",
        json={"email": "nobody@test.local", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


def test_update_user_blocks_sole_admin_self_demotion(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as ei:
        um_svc.update_user(db, actor=admin, target=admin, role=UserRole.employee)
    assert ei.value.code == "LAST_ADMIN"
    assert admin.role == UserRole.admin


def test_update_user_blocks_disabling_last_admin(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    # A disabled admin does not count toward the "enabled admins" floor.
    make_user(email="other@test.local", role=UserRole.admin, is_disabled=True)
    with pytest.raises(AppError) as ei:
        um_svc.update_user(db, actor=admin, target=admin, is_disabled=True)
    assert ei.value.code == "LAST_ADMIN"


def test_update_user_allows_demotion_with_another_admin(make_user, db):
    a1 = make_user(email="a1@test.local", role=UserRole.admin)
    a2 = make_user(email="a2@test.local", role=UserRole.admin)
    um_svc.update_user(db, actor=a1, target=a2, role=UserRole.employee)
    db.commit()
    assert a2.role == UserRole.employee


@pytest.mark.asyncio
async def test_api_token_rejected_when_account_locked(make_user, db, client):
    user = make_user(email="tok@test.local", role=UserRole.employee)
    _rec, plaintext = api_token_svc.create_token(db, owner=user, name="ci")
    db.commit()

    ok = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert ok.status_code == 200, ok.text

    user.locked_until = utc_now() + timedelta(minutes=15)
    db.commit()

    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert resp.status_code == 423
    assert resp.json()["code"] == "ACCOUNT_LOCKED"
