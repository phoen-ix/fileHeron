"""/api/auth/* endpoints.

The refresh token lives in an httpOnly cookie scoped to /api/auth so it is
NEVER attached to other API routes (uploads, downloads, etc.).
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_current_user, get_db
from ..middleware.errors import AppError
from ..models.user import User
from ..schemas.auth import (
    ForgotPasswordRequest,
    LoginRecoveryRequest,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    RegisterFromInviteRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from ..services import auth as auth_svc
from ..services import email as email_svc
from ..services import jwt_session
from ..services import rate_limit as rate_limit_svc
from ..services import settings_registry

router = APIRouter(prefix="/api/auth", tags=["auth"])

_REFRESH_COOKIE = "fh_refresh"
_REFRESH_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, plaintext: str, db: Session) -> None:
    # Match the cookie's client-side lifetime to the refresh token's
    # server-side expiry — both read the admin-tunable value (kv overlay,
    # env default) so an admin change keeps them in sync.
    from ..services import settings_registry
    days = settings_registry.effective(db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS)
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=plaintext,
        max_age=days * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path=_REFRESH_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path=_REFRESH_PATH)


@router.post("/register-from-invite", status_code=status.HTTP_200_OK, response_model=LoginResponse)
async def register_from_invite(
    payload: RegisterFromInviteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    ip = request.client.host if request.client else ""
    if not rate_limit_svc.check_ip_allowed(
        "register", ip, settings_registry.effective(db, settings_registry.K.RATE_LIMIT_REGISTER)
    ):
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")
    user = auth_svc.register_from_invite(
        db,
        plaintext_token=payload.token,
        password=payload.password,
        display_name=payload.display_name,
        locale=payload.locale,
        request=request,
    )
    # Issue session immediately so the new user is logged in.
    access, expires_in = auth_svc.create_access_token(user.id, settings, db)
    rate_limit_svc.record_success(db, user=user)
    _, refresh_plain = jwt_session.create_refresh_token(db, user, request, settings)
    db.commit()
    _set_refresh_cookie(response, refresh_plain, db)
    return LoginResponse(access_token=access, expires_in_seconds=expires_in)


@router.post("/login", status_code=status.HTTP_200_OK, response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    _user, access, expires_in, refresh_plain = await auth_svc.login(
        db,
        email=payload.email,
        password=payload.password,
        totp_code=payload.totp_code,
        request=request,
        settings=settings,
    )
    db.commit()
    _set_refresh_cookie(response, refresh_plain, db)
    return LoginResponse(access_token=access, expires_in_seconds=expires_in)


@router.post("/login/recovery", status_code=status.HTTP_200_OK, response_model=LoginResponse)
async def login_recovery(
    payload: LoginRecoveryRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Login using a recovery code instead of a TOTP code. The recovery code
    is consumed (one-time use)."""
    _user, access, expires_in, refresh_plain = await auth_svc.login_with_recovery(
        db,
        email=payload.email,
        password=payload.password,
        recovery_code=payload.recovery_code,
        request=request,
        settings=settings,
    )
    db.commit()
    _set_refresh_cookie(response, refresh_plain, db)
    return LoginResponse(access_token=access, expires_in_seconds=expires_in)


@router.post("/refresh", status_code=status.HTTP_200_OK, response_model=RefreshResponse)
def refresh(
    request: Request,
    response: Response,
    fh_refresh: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> RefreshResponse:
    if not fh_refresh:
        raise AppError(401, "AUTH_REQUIRED", "Refresh cookie missing.")
    _user, access, expires_in, new_refresh_plain = jwt_session.rotate_refresh(
        db, refresh_token_plain=fh_refresh, request=request, settings=settings
    )
    db.commit()
    _set_refresh_cookie(response, new_refresh_plain, db)
    return RefreshResponse(access_token=access, expires_in_seconds=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    fh_refresh: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Response:
    if fh_refresh:
        jwt_session.logout(db, refresh_token_plain=fh_refresh, request=request)
        db.commit()
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Always returns 200 — never reveals whether the email exists."""
    ip = request.client.host if request.client else ""
    if not rate_limit_svc.check_ip_allowed(
        "forgot", ip, settings_registry.effective(db, settings_registry.K.RATE_LIMIT_REGISTER)
    ):
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")
    result = auth_svc.begin_password_reset(db, email=payload.email, request=request)
    db.commit()
    if result is not None:
        user, plaintext = result
        from ..services import site as site_svc
        await email_svc.send_password_reset_email(
            to=payload.email, locale=user.locale, display_name=user.display_name,
            token=plaintext,
            app_url=site_svc.get_site_url(db),
            site_timezone=site_svc.get_site_timezone(db),
        )
    return {"ok": True}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    await auth_svc.consume_password_reset(
        db, plaintext_token=payload.token, new_password=payload.new_password, request=request
    )
    db.commit()
    # Force re-login: clear refresh cookie if present.
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.post("/verify-email", status_code=status.HTTP_200_OK)
def verify_email(payload: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else ""
    if not rate_limit_svc.check_ip_allowed(
        "verify", ip, settings_registry.effective(db, settings_registry.K.RATE_LIMIT_REGISTER)
    ):
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")
    auth_svc.consume_email_verification(db, plaintext_token=payload.token, request=request)
    db.commit()
    return {"ok": True}


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.email_verified:
        return {"ok": True, "already_verified": True}
    plaintext = auth_svc.begin_email_verification(db, user=user)
    db.commit()
    # We don't store plaintext email — but the invite/login flow knows the
    # user clicked through, so address resolution here uses an admin-only
    # path. For Phase 1a, log the link via the email service (which has
    # logs-fallback). The actual SMTP send to the user's email will be wired
    # up in a small follow-up: Phase 1a invites are pre-verified, so this
    # branch should be unreachable in practice.
    request.state.verify_link_for_dev = plaintext
    return {"ok": True}
