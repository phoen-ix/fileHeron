"""Email-change feature (v1.13.0): policy modes, OIDC reset, gates, masking.

Service-level tests drive the token flow directly (the request outcome carries
the plaintext tokens); API-level tests cover the endpoints + gates.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.email_change_token import EmailChangeToken
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.user import UserRole
from app.services import email_change as ec
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now


def _set_policy(db, *, mode=None, self_service=None, oidc_mode=None):
    if mode is not None:
        settings_svc.set_value(
            db, key=settings_svc.Keys.EMAIL_CHANGE_VERIFICATION_MODE, value=mode, actor=None
        )
    if self_service is not None:
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE,
            value="true" if self_service else "false",
            actor=None,
        )
    if oidc_mode is not None:
        settings_svc.set_value(
            db, key=settings_svc.Keys.EMAIL_CHANGE_OIDC_MODE, value=oidc_mode, actor=None
        )
    db.commit()


def _audit_count(db, event: AuditEventType) -> int:
    return (
        db.query(AuditLog).filter(AuditLog.event_type == event.value).count()
    )


def _bind_oidc(db, user, provider, sub="sub-abc"):
    user.oidc_provider_id = provider.id
    user.oidc_subject = sub
    db.commit()


# --- service: verification modes -------------------------------------------


def test_immediate_applies_at_once(make_user, db):
    _set_policy(db, mode="immediate")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="old@test.local", role=UserRole.client)

    out = ec.request_email_change(
        db, target=target, new_email="NEW@test.local", initiated_by=admin, request=None
    )
    db.commit()

    assert out.applied is True
    assert out.mode == "immediate"
    db.refresh(target)
    assert target.email == "new@test.local"  # normalized
    assert target.email_verified is True
    assert _audit_count(db, AuditEventType.email_changed) == 1
    # No pending token row for an immediate change.
    assert db.query(EmailChangeToken).count() == 0


def test_verify_new_stages_then_confirms(make_user, db):
    _set_policy(db, mode="verify_new")
    target = make_user(email="old@test.local", role=UserRole.client)

    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=target, request=None
    )
    db.commit()

    assert out.applied is False
    assert out.new_token and out.cancel_token and out.old_token is None
    db.refresh(target)
    assert target.email == "old@test.local"  # unchanged until confirmed
    assert _audit_count(db, AuditEventType.email_change_requested) == 1

    res = ec.confirm_email_change(db, token=out.new_token, request=None)
    db.commit()
    assert res.applied is True
    db.refresh(target)
    assert target.email == "new@test.local"
    assert target.email_verified is True
    assert _audit_count(db, AuditEventType.email_changed) == 1


def test_verify_both_needs_both_sides(make_user, db):
    _set_policy(db, mode="verify_both")
    target = make_user(email="old@test.local", role=UserRole.client)

    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=target, request=None
    )
    db.commit()
    assert out.old_token is not None  # verify_both mints an old-side token

    # New side alone does not apply.
    res1 = ec.confirm_email_change(db, token=out.new_token, request=None)
    db.commit()
    assert res1.applied is False
    assert res1.pending_side == "old"
    db.refresh(target)
    assert target.email == "old@test.local"

    # Old side completes it.
    res2 = ec.confirm_email_change(db, token=out.old_token, request=None)
    db.commit()
    assert res2.applied is True
    db.refresh(target)
    assert target.email == "new@test.local"


# --- service: validation + token lifecycle ---------------------------------


def test_unchanged_rejected(make_user, db):
    target = make_user(email="me@test.local", role=UserRole.client)
    with pytest.raises(Exception) as ei:
        ec.request_email_change(
            db, target=target, new_email="ME@test.local", initiated_by=target, request=None
        )
    assert getattr(ei.value, "code", None) == "EMAIL_UNCHANGED"


def test_taken_rejected(make_user, db):
    make_user(email="taken@test.local", role=UserRole.client)
    target = make_user(email="me@test.local", role=UserRole.client)
    with pytest.raises(Exception) as ei:
        ec.request_email_change(
            db, target=target, new_email="taken@test.local", initiated_by=target, request=None
        )
    assert getattr(ei.value, "code", None) == "EMAIL_TAKEN"


def test_confirm_expired_rejected(make_user, db):
    _set_policy(db, mode="verify_new")
    target = make_user(email="old@test.local", role=UserRole.client)
    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=target, request=None
    )
    # Force-expire the row.
    row = db.query(EmailChangeToken).one()
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(Exception) as ei:
        ec.confirm_email_change(db, token=out.new_token, request=None)
    assert getattr(ei.value, "code", None) == "EMAIL_CHANGE_TOKEN_EXPIRED"


def test_confirm_replay_rejected(make_user, db):
    _set_policy(db, mode="verify_new")
    target = make_user(email="old@test.local", role=UserRole.client)
    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=target, request=None
    )
    db.commit()
    ec.confirm_email_change(db, token=out.new_token, request=None)
    db.commit()
    with pytest.raises(Exception) as ei:
        ec.confirm_email_change(db, token=out.new_token, request=None)
    assert getattr(ei.value, "code", None) == "EMAIL_CHANGE_TOKEN_USED"


def test_request_supersedes_prior_pending(make_user, db):
    _set_policy(db, mode="verify_new")
    target = make_user(email="old@test.local", role=UserRole.client)
    first = ec.request_email_change(
        db, target=target, new_email="one@test.local", initiated_by=target, request=None
    )
    db.commit()
    ec.request_email_change(
        db, target=target, new_email="two@test.local", initiated_by=target, request=None
    )
    db.commit()
    # The first link is now dead.
    with pytest.raises(Exception) as ei:
        ec.confirm_email_change(db, token=first.new_token, request=None)
    assert getattr(ei.value, "code", None) == "EMAIL_CHANGE_TOKEN_CANCELLED"


def test_cancel_kills_pending(make_user, db):
    _set_policy(db, mode="verify_new")
    target = make_user(email="old@test.local", role=UserRole.client)
    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=target, request=None
    )
    db.commit()
    n = ec.cancel_email_change(db, token=out.cancel_token, request=None)
    db.commit()
    assert n == 1
    assert _audit_count(db, AuditEventType.email_change_cancelled) == 1
    with pytest.raises(Exception) as ei:
        ec.confirm_email_change(db, token=out.new_token, request=None)
    assert getattr(ei.value, "code", None) == "EMAIL_CHANGE_TOKEN_CANCELLED"


# --- service: OIDC reset modes ---------------------------------------------


def test_oidc_reset_setpw(make_user, db, make_provider):
    _set_policy(db, mode="immediate", oidc_mode="reset_setpw")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="sso@test.local", role=UserRole.client)
    provider = make_provider()
    _bind_oidc(db, target, provider)

    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=admin, request=None
    )
    db.commit()
    assert out.oidc_reset is True
    assert out.set_password_token is not None
    db.refresh(target)
    assert target.oidc_provider_id is None
    assert target.oidc_subject is None
    assert _audit_count(db, AuditEventType.oidc_unlinked) == 1
    assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == target.id).count() == 1


def test_oidc_reset_only(make_user, db, make_provider):
    _set_policy(db, mode="immediate", oidc_mode="reset_only")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="sso@test.local", role=UserRole.client)
    _bind_oidc(db, target, make_provider())

    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=admin, request=None
    )
    db.commit()
    assert out.oidc_reset is True
    assert out.set_password_token is None
    db.refresh(target)
    assert target.oidc_provider_id is None
    assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == target.id).count() == 0


def test_oidc_keep(make_user, db, make_provider):
    _set_policy(db, mode="immediate", oidc_mode="keep")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="sso@test.local", role=UserRole.client)
    provider = make_provider()
    _bind_oidc(db, target, provider)

    out = ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=admin, request=None
    )
    db.commit()
    assert out.oidc_reset is False
    db.refresh(target)
    assert target.oidc_provider_id == provider.id
    assert target.oidc_subject is not None


def test_apply_revokes_sessions(make_user, db):
    _set_policy(db, mode="immediate")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="old@test.local", role=UserRole.client)
    db.add(
        RefreshToken(user_id=target.id, token_hash="z" * 64, expires_at=utc_now() + timedelta(days=7))
    )
    db.commit()

    ec.request_email_change(
        db, target=target, new_email="new@test.local", initiated_by=admin, request=None
    )
    db.commit()
    active = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
        .count()
    )
    assert active == 0


# --- API: admin endpoint ----------------------------------------------------


@pytest.mark.asyncio
async def test_admin_change_email_verify_new_e2e(make_user, db, client, login_as):
    _set_policy(db, mode="verify_new")
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    target = make_user(email="old@test.local", role=UserRole.client, password="UserPass12345!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/admin/users/{target.id}/email",
        json={"new_email": "new@test.local"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is False
    assert body["confirm_url"] and "/confirm-email-change/" in body["confirm_url"]

    # Old email still logs in (change not yet applied).
    ok, _ = await login_as("old@test.local", "UserPass12345!")
    assert ok

    confirm_token = body["confirm_url"].split("/confirm-email-change/")[1]
    cr = await client.post("/api/auth/confirm-email-change", json={"token": confirm_token})
    assert cr.status_code == 200, cr.text
    assert cr.json()["applied"] is True

    # New email logs in; old email no longer does.
    new_ok = await client.post(
        "/api/auth/login", json={"email": "new@test.local", "password": "UserPass12345!"}
    )
    assert new_ok.status_code == 200, new_ok.text
    old_fail = await client.post(
        "/api/auth/login", json={"email": "old@test.local", "password": "UserPass12345!"}
    )
    assert old_fail.status_code == 401


@pytest.mark.asyncio
async def test_admin_change_email_immediate_skip(make_user, db, client, login_as):
    # Policy default (verify_new) but admin forces immediate via skip_verification.
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    target = make_user(email="old@test.local", role=UserRole.client)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/admin/users/{target.id}/email",
        json={"new_email": "new@test.local", "skip_verification": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is True
    db.refresh(target)
    assert target.email == "new@test.local"


@pytest.mark.asyncio
async def test_admin_change_email_taken_409(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    make_user(email="taken@test.local", role=UserRole.client)
    target = make_user(email="old@test.local", role=UserRole.client)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        f"/api/admin/users/{target.id}/email",
        json={"new_email": "taken@test.local"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMAIL_TAKEN"


@pytest.mark.asyncio
async def test_admin_change_email_non_admin_403(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin)
    actor = make_user(email="client@test.local", role=UserRole.client, password="ClientPass123!")
    token, _ = await login_as("client@test.local", "ClientPass123!")
    resp = await client.post(
        f"/api/admin/users/{actor.id}/email",
        json={"new_email": "x@test.local"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# --- API: self-service gates ------------------------------------------------


@pytest.mark.asyncio
async def test_self_service_disabled_by_default(make_user, db, client, login_as):
    make_user(email="u@test.local", role=UserRole.client, password="UserPass12345!")
    token, _ = await login_as("u@test.local", "UserPass12345!")
    resp = await client.post(
        "/api/account/email",
        json={"new_email": "new@test.local", "current_password": "UserPass12345!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "EMAIL_CHANGE_DISABLED"


@pytest.mark.asyncio
async def test_self_service_wrong_password_401(make_user, db, client, login_as):
    _set_policy(db, self_service=True)
    make_user(email="u@test.local", role=UserRole.client, password="UserPass12345!")
    token, _ = await login_as("u@test.local", "UserPass12345!")
    resp = await client.post(
        "/api/account/email",
        json={"new_email": "new@test.local", "current_password": "WRONGpassword1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_self_service_happy_path(make_user, db, client, login_as):
    _set_policy(db, mode="verify_new", self_service=True)
    user = make_user(email="u@test.local", role=UserRole.client, password="UserPass12345!")
    token, _ = await login_as("u@test.local", "UserPass12345!")
    resp = await client.post(
        "/api/account/email",
        json={"new_email": "new@test.local", "current_password": "UserPass12345!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is False
    db.refresh(user)
    assert user.email == "u@test.local"  # not applied until confirmed
    assert db.query(EmailChangeToken).filter(EmailChangeToken.user_id == user.id).count() == 1


@pytest.mark.asyncio
async def test_me_exposes_can_change_own_email(make_user, db, client, login_as):
    make_user(email="u@test.local", role=UserRole.client, password="UserPass12345!")
    token, _ = await login_as("u@test.local", "UserPass12345!")

    me1 = await client.get("/api/account/me", headers={"Authorization": f"Bearer {token}"})
    assert me1.json()["can_change_own_email"] is False

    _set_policy(db, self_service=True)
    me2 = await client.get("/api/account/me", headers={"Authorization": f"Bearer {token}"})
    assert me2.json()["can_change_own_email"] is True


# --- API: settings policy ---------------------------------------------------


@pytest.mark.asyncio
async def test_settings_get_put_and_audit(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    hdr = {"Authorization": f"Bearer {token}"}

    got = await client.get("/api/admin/settings/email-change", headers=hdr)
    assert got.status_code == 200
    assert got.json() == {
        "verification_mode": "verify_new",
        "self_service": False,
        "oidc_mode": "reset_setpw",
    }

    put = await client.put(
        "/api/admin/settings/email-change",
        json={"verification_mode": "verify_both", "self_service": True, "oidc_mode": "keep"},
        headers=hdr,
    )
    assert put.status_code == 200, put.text
    assert put.json()["verification_mode"] == "verify_both"
    assert put.json()["self_service"] is True
    assert _audit_count(db, AuditEventType.email_change_policy_changed) == 1

    # Invalid enum rejected by the schema.
    bad = await client.put(
        "/api/admin/settings/email-change",
        json={"verification_mode": "nonsense", "self_service": True, "oidc_mode": "keep"},
        headers=hdr,
    )
    assert bad.status_code == 422


# --- email templates render -------------------------------------------------


@pytest.mark.parametrize("loc", ["en", "de"])
@pytest.mark.parametrize(
    "slug",
    [
        "email_change_confirm",
        "email_change_verify_old",
        "email_change_alert",
        "email_change_completed",
    ],
)
def test_email_change_templates_render(loc, slug):
    from app.services.email import render_email

    ctx = {
        "display_name": "Jo",
        "new_email": "new@test.local",
        "by_admin": True,
        "applied": False,
        "oidc_reset": True,
        "confirm_url": "https://x.test/confirm-email-change/TOK",
        "cancel_url": "https://x.test/cancel-email-change/CAN",
        "reset_url": "https://x.test/forgot-password",
        "login_url": "https://x.test/login",
    }
    subject, text, _html = render_email(loc, slug, ctx)
    assert subject and subject != slug  # resolved from subjects.json
    assert text.strip()
