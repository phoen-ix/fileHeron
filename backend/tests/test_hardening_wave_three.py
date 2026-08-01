"""Auth disclosure, operator alerting, updates and config import - audit #2.

One test per finding; each names what it would have cost.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "CorrectHorse9!"


# --- the account-existence oracle -------------------------------------------


@pytest.mark.asyncio
async def test_a_locked_account_answers_401_to_a_wrong_password(client, db, make_user):
    """Six wrong guesses turned a real address into `423 ACCOUNT_LOCKED` with a
    `locked_until` timestamp, while an unknown address answered 401 all six
    times - one probe per address, inside a single per-IP window, no timing
    analysis needed. The same sequence also locked every confirmed account for
    15 minutes and mailed it a lockout warning, so it doubled as a targeted
    denial of service and a phishing pretext."""
    make_user(email="real@test.local", password=PW)
    db.commit()

    seen = set()
    for _ in range(10):
        r = await client.post(
            "/api/auth/login", json={"email": "real@test.local", "password": "nope"}
        )
        seen.add(r.status_code)
    assert seen == {401}, f"a locked account announced itself: {seen}"

    ghost = await client.post(
        "/api/auth/login", json={"email": "ghost@test.local", "password": "nope"}
    )
    assert ghost.status_code == 401


@pytest.mark.asyncio
async def test_the_owner_of_a_locked_account_is_still_told(client, db, make_user):
    """The control. The lockout must still be enforced AND explained to whoever
    can produce the password."""
    from app.models.user import User

    make_user(email="owner@test.local", password=PW)
    db.commit()
    for _ in range(10):
        await client.post(
            "/api/auth/login", json={"email": "owner@test.local", "password": "nope"}
        )
    db.expire_all()
    assert db.query(User).filter(User.email == "owner@test.local").one().locked_until

    r = await client.post(
        "/api/auth/login", json={"email": "owner@test.local", "password": PW}
    )
    assert r.status_code == 423
    assert r.json()["code"] == "ACCOUNT_LOCKED"
    assert r.json()["details"]["locked_until"]


# --- 2FA cannot become un-removable -----------------------------------------


def test_2fa_can_be_disabled_with_a_recovery_code_when_the_secret_is_unreadable(
    db, make_user, monkeypatch
):
    """The JWT_SECRET-rotated-without-re-encrypting case the error's own
    docstring names. Sign-in with a recovery code worked; disabling 2FA,
    minting fresh codes and every admin remedy did not - so after the tenth
    code the account was permanently unreachable through the API, and the 503
    named an admin remedy that does not exist."""
    from app.middleware.errors import AppError
    from app.models.user_recovery_code import UserRecoveryCode
    from app.models.user_totp import UserTOTP
    from app.services import totp as totp_svc
    from app.utils.crypto import argon2_hash
    from app.utils.timeutil import utc_now

    user = make_user(email="tf@test.local", password=PW)
    db.add(UserTOTP(user_id=user.id, secret_encrypted=b"unreadable", enabled_at=utc_now()))
    db.add(UserRecoveryCode(user_id=user.id, code_hash=argon2_hash("RECOVERY-1")))
    db.commit()
    db.refresh(user)

    def _boom(*_a, **_kw):
        raise AppError(503, "TOTP_SECRET_UNAVAILABLE", "unreadable")

    monkeypatch.setattr(totp_svc, "verify_at_login", _boom)

    totp_svc.disable(
        db, user=user, password=PW, code_or_recovery="RECOVERY-1", request=None
    )
    db.flush()
    assert db.query(UserTOTP).filter(UserTOTP.user_id == user.id).count() == 0


def test_a_wrong_recovery_code_still_fails(db, make_user, monkeypatch):
    """The control: an unreadable secret must not become a way past the gate."""
    from app.middleware.errors import AppError
    from app.models.user_totp import UserTOTP
    from app.services import totp as totp_svc
    from app.utils.timeutil import utc_now

    user = make_user(email="tf2@test.local", password=PW)
    db.add(UserTOTP(user_id=user.id, secret_encrypted=b"unreadable", enabled_at=utc_now()))
    db.commit()
    db.refresh(user)

    def _boom(*_a, **_kw):
        raise AppError(503, "TOTP_SECRET_UNAVAILABLE", "unreadable")

    monkeypatch.setattr(totp_svc, "verify_at_login", _boom)
    with pytest.raises(AppError) as exc:
        totp_svc.disable(
            db, user=user, password=PW, code_or_recovery="WRONG", request=None
        )
    assert exc.value.code == "INVALID_TOTP"


# --- the 500 response -------------------------------------------------------


def test_an_unhandled_500_carries_the_security_headers_and_the_request_id():
    """`add_exception_handler(Exception, ...)` is served by ServerErrorMiddleware,
    which sits OUTSIDE every user middleware - so a 500 went out with
    content-type and content-length and nothing else: no nosniff, no CSP, no
    X-Frame-Options, no HSTS in production, and no X-Request-Id. The SPA and the
    desktop client are both told to quote the request id when reporting a
    failure."""
    import inspect

    from app.middleware import errors

    src = inspect.getsource(errors.unhandled_exception_handler)
    assert "apply_security_headers" in src
    assert "X-Request-Id" in src


# --- alerting that reaches nobody -------------------------------------------


def test_enabling_alerts_with_an_empty_custom_list_is_refused(db, make_user):
    """Saving this stored a control that does nothing: the page renders
    "alerting: on", every 500 spends the cooldown and hourly-cap budget, and no
    email is sent. An instance can 500 for weeks behind a green settings page,
    with `alerted=0` on every row reading exactly like "throttled"."""
    from app.middleware.errors import AppError
    from app.services import error_alert

    admin = make_user(email="a@test.local", role=UserRole.admin)
    db.commit()
    with pytest.raises(AppError) as exc:
        error_alert.update_settings(
            db,
            enabled=True,
            source_http_5xx=True,
            source_http_4xx=False,
            recipients_mode="custom",
            custom_recipients=[],
            cooldown_minutes=15,
            max_per_hour=10,
            log_enabled=True,
            capture_4xx=False,
            http_4xx_codes=[],
            retention_days=90,
            actor=admin,
        )
    assert exc.value.code == "ALERT_RECIPIENTS_EMPTY"


# --- maintenance, updates, import -------------------------------------------


def test_a_stale_handoff_stamp_does_not_lift_manual_maintenance(db):
    """An operator turned maintenance on by hand to run a storage migration and
    the drain worker lifted it within 60 seconds, on the strength of a stamp
    from a failed hand-off weeks earlier, with no audit row explaining why."""
    from datetime import timedelta

    from app.services import maintenance
    from app.utils.timeutil import utc_now

    maintenance.set_enabled(db, True, actor=None)
    maintenance.set_handoff_at(
        db, (utc_now() - timedelta(days=14)).isoformat(), actor=None
    )
    db.commit()

    assert maintenance.clear_maintenance_after_update(db) is False
    assert maintenance.is_enabled(db) is True


def test_a_fresh_handoff_stamp_still_lifts_it(db):
    """The control: the hand-off this stamp exists for must still work."""
    from app.services import maintenance
    from app.utils.timeutil import utc_now

    maintenance.set_enabled(db, True, actor=None)
    maintenance.set_handoff_at(db, utc_now().isoformat(), actor=None)
    db.commit()

    assert maintenance.clear_maintenance_after_update(db) is True
    assert maintenance.is_enabled(db) is False


def test_the_executor_recreates_the_shim_after_the_job_is_terminal():
    """"The perpetual shim never updates itself" held until v2.5.0 shipped a fix
    TO the shim, which then could not reach a single instance. Ordering is
    load-bearing: the new shim's startup sweep marks any non-terminal job
    failed."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "docker" / "updater-executor" / "run.py"
    ).read_text()
    tail = src.split('write_job_field(status="healthy"')[-1]
    assert '"up", "-d", "updater-shim"' in tail, (
        "the shim is never recreated, so a shim fix cannot reach any instance"
    )
    head = src.split('write_job_field(status="healthy"')[0]
    assert '"up", "-d", "updater-shim"' not in head, (
        "recreating the shim mid-job makes its replacement fail the job"
    )
