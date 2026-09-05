"""WebAuthn / passkey endpoints (Phase 8).

Two ceremony pairs:

- Registration (authed): /api/account/webauthn/register/begin →
  /api/account/webauthn/register/complete
- Login (no auth required for begin; returns a temporary session
  string the client passes back on complete):
  /api/auth/webauthn/begin → /api/auth/webauthn/complete

The login flow gates on a successful password check first - passkeys
in this codebase are an *additional* factor (or a TOTP replacement),
not a passwordless first-factor. That's a deliberate scope choice;
true passwordless can be a follow-up.

A passkey replaces TOTP only when the authenticator reports USER
VERIFICATION (PIN / biometric): password + UV-verified passkey is two
factors. A bare-touch assertion is possession alone, so an account with TOTP
enabled gets a pending token from /complete and is asked for its code.
Because a passkey can stand in for the second factor, registering one is
gated on the current password (step-up), exactly like disabling TOTP.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_current_user, get_db
from ..middleware.errors import AppError
from ..models.user import User
from ..schemas.auth import WebAuthnAuthCompleteResponse
from ..schemas.webauthn import (
    WebAuthnAuthBeginRequest,
    WebAuthnAuthBeginResponse,
    WebAuthnAuthCompleteRequest,
    WebAuthnCredentialItem,
    WebAuthnCredentialListResponse,
    WebAuthnRegisterBeginRequest,
    WebAuthnRegisterBeginResponse,
    WebAuthnRegisterCompleteRequest,
)
from ..services import auth as auth_svc
from ..services import jwt_session, settings_registry
from ..services import rate_limit as rate_limit_svc
from ..services import totp as totp_svc
from ..services import webauthn as webauthn_svc
from ..services.audit import record_audit_event

logger = logging.getLogger("fileheron.webauthn_router")

# Two routers in one module - different prefixes.
account_router = APIRouter(prefix="/api/account/webauthn", tags=["webauthn"])
auth_router = APIRouter(prefix="/api/auth/webauthn", tags=["webauthn"])


def _to_item(c) -> WebAuthnCredentialItem:
    return WebAuthnCredentialItem(
        id=c.id,
        name=c.name,
        transports=[t for t in c.transports.split(",") if t],
        created_at=c.created_at,
        last_used_at=c.last_used_at,
    )


# ---- Account: list / register / delete ----------------------------------


@account_router.get("", response_model=WebAuthnCredentialListResponse)
def list_credentials(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredentialListResponse:
    creds = webauthn_svc.list_credentials_for(db, user.id)
    return WebAuthnCredentialListResponse(items=[_to_item(c) for c in creds])


@account_router.post(
    "/register/begin", response_model=WebAuthnRegisterBeginResponse
)
async def register_begin(
    payload: WebAuthnRegisterBeginRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnRegisterBeginResponse:
    # Step-up BEFORE the browser prompt: a wrong password must not cost the
    # user a platform-authenticator dialog. Same gate as /2fa/disable and
    # API-token minting - it throttles per user and audits a failure.
    from ..services.step_up import verify_password_or_403

    verify_password_or_403(db, user, payload.password, request=request)
    options = await webauthn_svc.register_begin(db, user=user)
    db.commit()
    return WebAuthnRegisterBeginResponse(options=options)


@account_router.post(
    "/register/complete", response_model=WebAuthnCredentialItem
)
async def register_complete(
    payload: WebAuthnRegisterCompleteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnCredentialItem:
    record = await webauthn_svc.register_complete(
        db,
        user=user,
        credential_response=payload.credential,
        name=payload.name,
    )
    from ..models.audit_log import AuditEventType

    record_audit_event(
        db,
        event_type=AuditEventType.webauthn_credential_added,
        actor_user_id=user.id,
        target_type="webauthn_credential",
        target_id=record.id,
        metadata={"name": record.name, "via": "webauthn_register"},
        request=request,
    )
    db.commit()
    return _to_item(record)


@account_router.delete(
    "/{credential_db_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_credential(
    credential_db_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    webauthn_svc.delete_credential(
        db, user=user, credential_db_id=credential_db_id
    )
    from ..models.audit_log import AuditEventType

    record_audit_event(
        db,
        event_type=AuditEventType.webauthn_credential_removed,
        actor_user_id=user.id,
        target_type="webauthn_credential",
        target_id=credential_db_id,
        metadata={"via": "webauthn_delete"},
        request=request,
    )
    db.commit()


# ---- Auth: passkey-instead-of-TOTP login --------------------------------


@auth_router.post("/begin", response_model=WebAuthnAuthBeginResponse)
async def auth_begin(
    payload: WebAuthnAuthBeginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> WebAuthnAuthBeginResponse:
    """Validate the email/password through the SAME pre-second-factor gate as
    /api/auth/login (per-IP rate limit, account lockout, password verify with
    failure recording + lockout email, disabled/email-verified checks) but,
    instead of issuing tokens, return a passkey challenge. The client completes
    the flow by calling /complete with the same `session`. Sharing the gate is
    what closes the previously-unthrottled password + enumeration oracle on
    this endpoint (audit H1)."""
    user = await auth_svc.authenticate_first_factor(
        db, email=payload.email, password=payload.password, request=request
    )

    session_key = secrets.token_urlsafe(24)
    # With TOTP enabled the assertion has to carry user verification to count
    # as the second factor, so ask the browser to REQUIRE it: an authenticator
    # that cannot verify the user fails the ceremony on the client instead of
    # producing a possession-only assertion that would only earn a pending
    # token and the TOTP prompt the user was already looking at.
    options = await webauthn_svc.authenticate_begin(
        db,
        user=user,
        session_key=session_key,
        require_user_verification=totp_svc.is_enabled(user),
    )
    db.commit()
    return WebAuthnAuthBeginResponse(session=session_key, options=options)


# exclude_none: the two shapes are mutually exclusive and the ABSENCE is the
# contract - a half-authenticated response must not carry an `access_token`
# key at all, which is what test_a_passkey_login_also_challenges_totp pins.
# Without it the model serialises `"access_token": null` into a reply whose
# whole point is that no session was minted.
@auth_router.post(
    "/complete",
    response_model=WebAuthnAuthCompleteResponse,
    response_model_exclude_none=True,
)
async def auth_complete(
    payload: WebAuthnAuthCompleteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    result = await webauthn_svc.authenticate_complete(
        db,
        session_key=payload.session,
        credential_response=payload.credential,
    )
    user = result.user

    # The /begin gate verified account state, but that was a prior request -
    # re-check here so a passkey ceremony can't mint a session for an account
    # disabled/locked/unverified in the interim (audit L3).
    if user.is_disabled:
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")
    if rate_limit_svc.is_account_locked(user):
        raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
    if not user.email_verified:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email first.")

    # A passkey is a second factor only when the authenticator VERIFIED the
    # user (PIN / biometric - the UV flag on the assertion). /begin asks for
    # REQUIRED when TOTP is on, but the flag is what is checked here: a
    # possession-only assertion earns a pending token and the TOTP prompt -
    # otherwise enabling 2FA would silently do nothing for anyone who signs in
    # with a passkey, exactly as it did nothing for SSO before this wave.
    if totp_svc.is_enabled(user) and not result.user_verified:
        # record_success deliberately withheld until the second factor passes:
        # it clears failed_login_count and locked_until.
        pending = jwt_session.create_pending_2fa_token(user.id, settings, via="webauthn")
        db.commit()
        # Shape mirrors the success return: the SPA branches on which key is
        # present rather than on a status code (stores/auth.ts::loginWithPasskey
        # routes a pending token to the /login/2fa interstitial).
        return {"pending_2fa_token": pending}

    # Mint the same session + forensic trail the password flow produces.
    rate_limit_svc.record_success(db, user=user)
    access, expires_in, refresh_plain = auth_svc.finalize_successful_login(
        db, user=user, request=request, settings=settings, via="webauthn",
    )
    db.commit()

    response.set_cookie(
        key="fh_refresh",
        value=refresh_plain,
        max_age=settings_registry.effective(db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS) * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )
    return {"access_token": access, "expires_in_seconds": expires_in}
