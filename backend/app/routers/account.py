"""/api/account/* endpoints - self-service for the authenticated user."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..dependencies import get_actor, get_current_admin, get_current_user, get_db
from ..middleware.errors import AppError
from ..models.api_token import ApiToken
from ..models.user import User, UserRole
from ..models.user_recovery_code import UserRecoveryCode
from ..schemas.account import (
    ChangePasswordRequest,
    InviteRequest,
    MeResponse,
    RequestEmailChangeRequest,
    UpdateAdminNavModeRequest,
    UpdateAdminNavOpenRequest,
    UpdateDefaultLandingPageRequest,
    UpdateDisplayNameRequest,
    UpdateLocaleRequest,
)
from ..schemas.api_token import (
    ApiTokenListItem,
    ApiTokenListResponse,
    CreateApiTokenRequest,
    CreateApiTokenResponse,
    CurrentApiTokenResponse,
)
from ..schemas.two_factor import (
    RecoveryCodeRegenerateRequest,
    RecoveryCodesResponse,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpSetupResponse,
    TotpStatusResponse,
)
from ..services import api_token as api_token_svc
from ..services import auth as auth_svc
from ..services import email as email_svc
from ..services import invite as invite_svc
from ..services import rate_limit as rate_limit_svc
from ..services import settings_registry
from ..services import totp as totp_svc

router = APIRouter(prefix="/api/account", tags=["account"])


def _me_response(db: Session, user: User) -> MeResponse:
    """Build a MeResponse with policy-derived fields populated. Reused
    by /me + the PATCH endpoints so a fresh bootstrap and a self-edit
    return the same shape."""
    from ..services import public_link as public_link_svc
    from ..services import settings as settings_svc
    from ..services import twofa_policy as twofa_policy_svc

    me_resp = MeResponse.model_validate(user)
    me_resp.can_create_public_link = public_link_svc.is_allowed_to_create(db, user)
    me_resp.home_page_enabled = settings_svc.get_bool(
        db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True
    )
    me_resp.requires_2fa = twofa_policy_svc.is_2fa_required(db, user)
    me_resp.share_notify_recipients_default = settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT, default=True
    )
    me_resp.file_preview_enabled = settings_svc.get_bool(
        db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
    )
    from ..services import share_approval as share_approval_svc

    me_resp.can_approve_shares = share_approval_svc.can_approve(db, user)
    from ..services import email_change_policy

    me_resp.can_change_own_email = email_change_policy.self_service_enabled(db)
    return me_resp


@router.get("/me", response_model=MeResponse)
def me(
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Bearer's own profile. Accepts both JWT (browser session) and
    API token bearers - the response is identical for both, and an
    API token is just a stable bearer for the same User principal."""
    return _me_response(db, user)


@router.patch("/locale", response_model=MeResponse)
def update_locale(
    payload: UpdateLocaleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    user.locale = payload.locale
    db.commit()
    db.refresh(user)
    return _me_response(db, user)


@router.patch("/display-name", response_model=MeResponse)
def update_display_name(
    payload: UpdateDisplayNameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    # Trim whitespace; the Field length check passes pure whitespace,
    # so we re-validate after the trim.
    name = payload.display_name.strip()
    if not name:
        raise AppError(
            422,
            "INVALID_DISPLAY_NAME",
            "Display name must contain at least one non-whitespace character.",
        )
    if len(name) > 120:
        raise AppError(
            422,
            "INVALID_DISPLAY_NAME",
            "Display name must be at most 120 characters.",
        )
    user.display_name = name
    db.commit()
    db.refresh(user)
    return _me_response(db, user)


@router.patch("/default-landing-page", response_model=MeResponse)
def update_default_landing_page(
    payload: UpdateDefaultLandingPageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Save the user's preferred post-login destination. `null` clears
    the preference (system default takes over). Otherwise the route
    name must be in `ALLOWED_LANDING_ROUTES`, and must not be `home`
    when the admin has disabled the home page."""
    from ..services import account_prefs
    from ..services import settings as settings_svc

    name = payload.default_landing_page
    if name is not None:
        if name not in account_prefs.ALLOWED_LANDING_ROUTES:
            raise AppError(
                400,
                "INVALID_LANDING_PAGE",
                "Unknown landing page name.",
                details={"value": name},
            )
        if name == "home" and not settings_svc.get_bool(
            db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True
        ):
            raise AppError(
                400,
                "HOME_PAGE_DISABLED",
                "The home page is currently disabled by the administrator.",
            )

    user.default_landing_page = name
    db.commit()
    db.refresh(user)
    return _me_response(db, user)


@router.patch("/admin-nav-mode", response_model=MeResponse)
def update_admin_nav_mode(
    payload: UpdateAdminNavModeRequest,
    user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Save the admin's preferred sidebar collapse mode. `null` clears the
    preference (system default = accordion takes over). Changing the mode
    resets `admin_nav_open_categories` to NULL so the new mode's default
    open-set applies on the next render."""
    from ..models.user import AdminNavCollapseMode
    from ..services import account_prefs

    mode = payload.mode
    if mode is not None and mode not in account_prefs.ADMIN_NAV_MODES:
        raise AppError(
            400,
            "INVALID_ADMIN_NAV_MODE",
            "Unknown admin sidebar mode.",
            details={"value": mode},
        )

    user.admin_nav_collapse_mode = (
        AdminNavCollapseMode(mode) if mode is not None else None
    )
    # Mode change invalidates the persisted open-set; reset to NULL so the
    # new mode's default applies.
    user.admin_nav_open_categories = None
    db.commit()
    db.refresh(user)
    return _me_response(db, user)


@router.patch("/admin-nav-open", response_model=MeResponse)
def update_admin_nav_open(
    payload: UpdateAdminNavOpenRequest,
    user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Save the set of currently-open sidebar category keys, synced across
    devices. An empty list is a valid explicit value (all collapsed) and is
    stored as `[]` (distinct from NULL = never set)."""
    from ..services import account_prefs

    keys = set(payload.open)
    unknown = sorted(keys - account_prefs.ADMIN_NAV_CATEGORIES)
    if unknown:
        raise AppError(
            400,
            "INVALID_ADMIN_NAV_CATEGORY",
            "Unknown sidebar category key.",
            details={"invalid": unknown},
        )

    # De-dupe + normalize to a deterministic order so the stored JSON is
    # comparable across writes.
    user.admin_nav_open_categories = [
        k for k in account_prefs.ADMIN_NAV_CATEGORIES_ORDER if k in keys
    ]
    db.commit()
    db.refresh(user)
    return _me_response(db, user)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else ""
    if not rate_limit_svc.check_ip_allowed(
        "change_password", ip,
        settings_registry.effective(db, settings_registry.K.RATE_LIMIT_REGISTER),
    ):
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")
    await auth_svc.change_password(
        db,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request=request,
    )
    db.commit()
    return {"ok": True}


@router.post("/email", status_code=status.HTTP_200_OK)
async def change_email(
    payload: RequestEmailChangeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Self-service email change. Gated on the `email_change.self_service`
    policy; re-authenticates with the current password; then follows the
    configured verification mode (a confirm link is mailed - the new address
    must click it, and in verify_both the old one too)."""
    from ..services import email_change as email_change_svc
    from ..services import email_change_policy
    from ..utils.crypto import argon2_verify

    if not email_change_policy.self_service_enabled(db):
        raise AppError(
            403,
            "EMAIL_CHANGE_DISABLED",
            "Changing your own email is disabled by the administrator.",
        )
    ip = request.client.host if request.client else ""
    if not rate_limit_svc.check_ip_allowed(
        "change_password", ip,
        settings_registry.effective(db, settings_registry.K.RATE_LIMIT_REGISTER),
    ):
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")
    if not argon2_verify(user.password_hash, payload.current_password):
        raise AppError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")

    outcome = email_change_svc.request_email_change(
        db,
        target=user,
        new_email=str(payload.new_email),
        initiated_by=user,
        request=request,
        skip_verification=False,
    )
    db.commit()
    await email_change_svc.dispatch_request_emails(db, outcome)
    return {"ok": True, "applied": outcome.applied, "mode": outcome.mode}


@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role not in (UserRole.admin, UserRole.employee):
        raise AppError(403, "FORBIDDEN", "Only employees and admins can invite.")

    # Duplicate-check 1: a real account already exists for this email.
    from ..utils.crypto import normalize_email

    em_hash = normalize_email(payload.email)
    existing = db.query(User).filter(User.email == em_hash).one_or_none()
    if existing is not None:
        raise AppError(
            409,
            "USER_EXISTS",
            "An account already exists for this email.",
            details={"email": existing.email},
        )

    # Duplicate-check 2: an outstanding invite is still pending.
    if invite_svc.has_pending_invite(db, email_value=em_hash):
        raise AppError(
            409,
            "INVITE_PENDING",
            "An invite for this email is already pending - wait for it to expire or be consumed.",
        )

    # Validate all initial_group_ids exist before creating the invite.
    initial_group_ids = list(payload.initial_group_ids or [])
    if initial_group_ids:
        from ..models.group import Group

        found = (
            db.query(Group.id)
            .filter(Group.id.in_(initial_group_ids))
            .all()
        )
        found_ids = {row[0] for row in found}
        missing = [gid for gid in initial_group_ids if gid not in found_ids]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    record, plaintext = invite_svc.create_invite(
        db,
        email=payload.email,
        target_role=payload.target_role,
        created_by=user,
        initial_group_ids=initial_group_ids or None,
    )
    # Audit
    from ..models.audit_log import AuditEventType
    from ..services.audit import record_audit_event
    record_audit_event(
        db,
        event_type=AuditEventType.invite_created,
        actor_user_id=user.id,
        target_type="invite",
        target_id=record.id,
        metadata={
            "target_role": payload.target_role.value,
            "email": record.email,
            "initial_group_ids": initial_group_ids,
        },
        request=request,
    )
    db.commit()

    from ..services import site as site_svc
    await email_svc.send_invite_email(
        to=payload.email,
        locale=user.locale,
        display_name_hint=payload.display_name_hint,
        inviter_display_name=user.display_name,
        token=plaintext,
        app_url=site_svc.get_site_url(db),
        site_timezone=site_svc.get_site_timezone(db),
        db=db,
    )
    return {"ok": True, "email": record.email, "expires_at": record.expires_at.isoformat()}


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------


@router.get("/2fa/status", response_model=TotpStatusResponse)
def totp_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TotpStatusResponse:
    enabled = totp_svc.is_enabled(user)
    enabled_at = user.totp.enabled_at if user.totp else None
    remaining = (
        db.query(UserRecoveryCode)
        .filter(UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None))
        .count()
    )
    return TotpStatusResponse(enabled=enabled, enabled_at=enabled_at, recovery_codes_remaining=remaining)


@router.post("/2fa/setup", response_model=TotpSetupResponse)
def totp_setup(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TotpSetupResponse:
    payload = totp_svc.begin_setup(db, user=user, request=request)
    db.commit()
    return TotpSetupResponse(**payload)


@router.post("/2fa/enable", response_model=RecoveryCodesResponse)
def totp_enable(
    payload: TotpEnableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    codes = totp_svc.confirm_enable(db, user=user, code=payload.code, request=request)
    db.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/2fa/disable", status_code=status.HTTP_200_OK)
def totp_disable(
    payload: TotpDisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    totp_svc.disable(
        db,
        user=user,
        password=payload.password,
        code_or_recovery=payload.code_or_recovery,
        request=request,
    )
    db.commit()
    return {"ok": True}


@router.post("/2fa/recovery-codes/regenerate", response_model=RecoveryCodesResponse)
def totp_recovery_regenerate(
    payload: RecoveryCodeRegenerateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    codes = totp_svc.regenerate_recovery_codes(
        db,
        user=user,
        password=payload.password,
        code_or_recovery=payload.code_or_recovery,
        request=request,
    )
    db.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


# ---------------------------------------------------------------------------
# API tokens (Phase 3a)
# ---------------------------------------------------------------------------


@router.post("/api-tokens", response_model=CreateApiTokenResponse, status_code=status.HTTP_201_CREATED)
def create_api_token(
    payload: CreateApiTokenRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateApiTokenResponse:
    if not api_token_svc.is_allowed_to_create(db, user):
        raise AppError(
            403,
            "API_TOKEN_NOT_ALLOWED",
            "Your administrator has restricted API token creation.",
        )
    expires_at = api_token_svc.normalize_expiry(payload.expires_at)
    record, plaintext = api_token_svc.create_token(
        db, owner=user, name=payload.name, expires_at=expires_at
    )

    from ..models.audit_log import AuditEventType
    from ..services.audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.api_token_created,
        actor_user_id=user.id,
        target_type="api_token",
        target_id=record.id,
        metadata={"name": record.name},
        request=request,
    )
    db.commit()
    return CreateApiTokenResponse(
        id=record.id,
        name=record.name,
        last4=record.last4,
        plaintext_token=plaintext,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("/api-tokens", response_model=ApiTokenListResponse)
def list_api_tokens(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiTokenListResponse:
    rows = api_token_svc.list_tokens(db, owner=user)
    return ApiTokenListResponse(
        items=[
            ApiTokenListItem(
                id=r.id,
                name=r.name,
                last4=r.last4,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                expires_at=r.expires_at,
            )
            for r in rows
        ],
        can_create=api_token_svc.is_allowed_to_create(db, user),
    )


@router.get("/api-tokens/current", response_model=CurrentApiTokenResponse)
def get_current_api_token(
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> CurrentApiTokenResponse:
    """Metadata for the API token authenticating THIS request, so a client
    (e.g. the desktop app) can show the user which token it's running on and
    point them at where to revoke it. JWT/session auth has no token → 400."""
    if getattr(request.state, "auth_via", None) != "api_token":
        raise AppError(
            400, "NOT_API_TOKEN", "Current authentication is not an API token."
        )
    token_id = getattr(request.state, "api_token_id", None)
    record = (
        db.query(ApiToken)
        .filter(ApiToken.id == token_id, ApiToken.owner_user_id == user.id)
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "TOKEN_NOT_FOUND", "API token not found.")
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    if record.revoked_at is not None:
        token_status = "revoked"
    elif record.expires_at is not None and now > record.expires_at:
        token_status = "expired"
    elif record.disabled_at is not None:
        token_status = "disabled"
    else:
        token_status = "active"
    return CurrentApiTokenResponse(
        id=record.id,
        name=record.name,
        last4=record.last4,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        expires_at=record.expires_at,
        status=token_status,
    )


@router.delete("/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_token(
    token_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    api_token_svc.revoke_token(db, owner=user, token_id=token_id)

    from ..models.audit_log import AuditEventType
    from ..services.audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.api_token_revoked,
        actor_user_id=user.id,
        target_type="api_token",
        target_id=token_id,
        request=request,
    )
    db.commit()
