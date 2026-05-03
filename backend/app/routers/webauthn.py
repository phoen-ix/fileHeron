"""WebAuthn / passkey endpoints (Phase 8).

Two ceremony pairs:

- Registration (authed): /api/account/webauthn/register/begin →
  /api/account/webauthn/register/complete
- Login (no auth required for begin; returns a temporary session
  string the client passes back on complete):
  /api/auth/webauthn/begin → /api/auth/webauthn/complete

The login flow gates on a successful password check first — passkeys
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
from ..services import webauthn as webauthn_svc
from ..services.audit import record_audit_event
from ..utils.crypto import argon2_verify, normalize_email

logger = logging.getLogger("fileheron.webauthn_router")

# Two routers in one module — different prefixes.
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
    """Validate the email/password (same gates as /api/auth/login) but
    instead of issuing tokens, return a passkey challenge. The client
    completes the flow by calling /complete with the same `session`."""
    em_hash = normalize_email(payload.email)
    user = db.query(User).filter(User.email == em_hash).one_or_none()
    if user is None or user.is_disabled:
        # Tarpit + uniform error to avoid email enumeration.
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    if not argon2_verify(user.password_hash, payload.password):
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")

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

    # Mint the same session cookies the password flow produces.
    access, expires_in = auth_svc.create_access_token(user.id, settings)
    _, refresh_plain = auth_svc._create_refresh_token(db, user, request, settings)
    db.commit()

    response.set_cookie(
        key="fh_refresh",
        value=refresh_plain,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )
    return {"access_token": access, "expires_in_seconds": expires_in}
