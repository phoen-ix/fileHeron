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
from ..schemas.webauthn import (
    WebAuthnAuthBeginRequest,
    WebAuthnAuthBeginResponse,
    WebAuthnAuthCompleteRequest,
    WebAuthnCredentialItem,
    WebAuthnCredentialListResponse,
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebAuthnRegisterBeginResponse:
    options = await webauthn_svc.register_begin(db, user=user)
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
        event_type=AuditEventType.totp_enabled,  # closest available; webauthn-specific event added in P8 cleanup
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
        event_type=AuditEventType.totp_disabled,  # closest available
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
    options = await webauthn_svc.authenticate_begin(
        db, user=user, session_key=session_key
    )
    db.commit()
    return WebAuthnAuthBeginResponse(session=session_key, options=options)


@auth_router.post("/complete")
async def auth_complete(
    payload: WebAuthnAuthCompleteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = await webauthn_svc.authenticate_complete(
        db,
        session_key=payload.session,
        credential_response=payload.credential,
    )

    # The /begin gate verified account state, but that was a prior request -
    # re-check here so a passkey ceremony can't mint a session for an account
    # disabled/locked/unverified in the interim (audit L3).
    if user.is_disabled:
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")
    if rate_limit_svc.is_account_locked(user):
        raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
    if not user.email_verified:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email first.")

    # A passkey is not automatically a second factor: /begin asks for
    # UserVerificationRequirement.PREFERRED and verification runs with
    # require_user_verification=False, so the ceremony may well have been a
    # single possession factor. If the account has TOTP enabled, demand it -
    # otherwise enabling 2FA silently did nothing for anyone who signs in with
    # a passkey, exactly as it did nothing for SSO before this wave.
    if totp_svc.is_enabled(user):
        # record_success deliberately withheld until the second factor passes:
        # it clears failed_login_count and locked_until.
        pending = jwt_session.create_pending_2fa_token(user.id, settings, via="webauthn")
        db.commit()
        # Shape mirrors the success return: the SPA branches on which key is
        # present rather than on a status code.
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
