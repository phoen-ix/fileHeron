"""/api/account/* endpoints - self-service for the authenticated user."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import settings
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

# Routes that MUST stay reachable while the 2FA policy is blocking the user,
# or they could never satisfy it: read /me to learn the requirement, run the
# TOTP enrolment, and switch language on the blocking screen.
#
# Everything else in this module is on `router`, which main.py mounts behind
# require_2fa_complete. main.py used to mount the whole module ungated with the
# comment "/me + /2fa/* must be reachable" - which also exempted /invite and
# the API-token endpoints. Since require_2fa_complete short-circuits for
# api_token auth, a user the policy covered could log in with a password, mint
# a token here, and use it on every gated route: mandatory 2FA was advisory and
# "no admin escape" was false (audit 2026-07-30). Keep this list minimal, and
# see tests/test_2fa_enforcement.py, which asserts the split stays honest.
setup_router = APIRouter(prefix="/api/account", tags=["account-setup"])


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

    # Authorization AND something to do: an admin may always decide (so a queue
    # left behind by switching the feature off is still clearable), but the
    # Approvals nav entry should not follow them around an instance that does
    # not use approvals.
    me_resp.can_approve_shares = share_approval_svc.can_approve(db, user) and (
        share_approval_svc.is_enabled(db)
        or share_approval_svc.has_pending_shares(db)
    )
    from ..services import email_change_policy

    me_resp.can_change_own_email = email_change_policy.self_service_enabled(db)
    return me_resp


@setup_router.get("/me", response_model=MeResponse)
def me(
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Bearer's own profile. Accepts both JWT (browser session) and
    API token bearers - the response is identical for both, and an
    API token is just a stable bearer for the same User principal."""
    return _me_response(db, user)


@setup_router.patch("/locale", response_model=MeResponse)
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
    # `.strip()` only removes SURROUNDING whitespace, so "Bob\nEvil" survived.
    # Three notification subjects interpolate the sender's display name, and
    # EmailMessage raises ValueError on a header value containing CR/LF - so a
    # newline here did not inject a header, it silently killed the notification
    # email sent to OTHER people (audit 2026-07-30). Reject control characters
    # outright; no legitimate display name contains one.
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise AppError(
            422,
            "INVALID_DISPLAY_NAME",
            "Display name must not contain control characters.",
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
    response: Response,
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
    # Security alert to the account owner (audit L34). Best-effort: a failed
    # alert must never fail the password change itself.
    try:
        import logging

        from ..services import site as site_svc
        from ..utils.geohash import ip_geohash5

        geo = ip_geohash5(ip) if ip else None
        await email_svc.send_password_changed_email(
            to=user.email,
            locale=user.locale,
            display_name=user.display_name,
            ip_hint=f"~{geo}" if geo else None,
            app_url=site_svc.get_site_url(db),
            site_timezone=site_svc.get_site_timezone(db),
            db=db,
        )
    except Exception:
        logging.getLogger("fileheron.account").exception(
            "password-changed alert failed for user=%d", user.id
        )

    # Re-establish THIS device's session. `change_password` revokes every
    # refresh token, including the caller's own - so the tab kept working on its
    # unexpired access token and was then bounced to /login up to
    # ACCESS_TOKEN_EXPIRE_MINUTES later, mid-task, losing whatever the user was
    # composing. The SPA comment at the call site asserted the opposite (audit
    # #2). Every OTHER device stays signed out, which is the point of the
    # revocation.
    access, expires_in, refresh_plain = auth_svc.finalize_successful_login(
        db, user=user, request=request, settings=settings,
        via="password_change", notify_new_device=False,
    )
    db.commit()
    response.set_cookie(
        key="fh_refresh",
        value=refresh_plain,
        max_age=settings_registry.effective(
            db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
        ) * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )
    return {"ok": True, "access_token": access, "expires_in_seconds": expires_in}


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
    # `_averify`, not `argon2_verify`: this is an `async def`, so a bare verify
    # holds the event loop for the duration of a 64 MiB KDF.
    if not await auth_svc._averify(user.password_hash, payload.current_password):
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


@router.delete("/email", status_code=status.HTTP_200_OK)
def cancel_own_email_change(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Withdraw my own pending email change(s).

    `cancel_email_change`'s `user=` branch has existed since the feature
    shipped, documented in its own docstring as "self/admin revoke", with no
    endpoint reaching it: someone who typed the wrong address had to wait 24h
    for the token to expire or find the cancel link mailed to their old address
    (audit 2026-07-30, flow-emailchange-8)."""
    from ..services import email_change as email_change_svc

    count = email_change_svc.cancel_email_change(db, user=user, request=request)
    db.commit()
    return {"ok": True, "cancelled": count}


@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role not in (UserRole.admin, UserRole.employee):
        raise AppError(403, "FORBIDDEN", "Only employees and admins can invite.")

    # An employee may only invite CLIENTS. `target_role` rides untouched through
    # invite_svc.create_invite -> InviteToken.target_role -> User(role=...) at
    # consume time, so without this clamp any employee could invite an address
    # they control with target_role=admin and hand themselves the admin shell,
    # config-backup export and GDPR erasure (audit 2026-07-30). Every other
    # role-granting surface (POST/PATCH /api/admin/users) is admin-gated; this
    # was the one hole, and the SPA only renders the form on the admin page so
    # it was invisible from the UI.
    if user.role != UserRole.admin and payload.target_role != UserRole.client:
        raise AppError(
            403,
            "INVITE_ROLE_NOT_ALLOWED",
            "Only admins can invite employees or admins.",
        )

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

        # Existence is not authority. Group membership is resolved LIVE by
        # services/share.py::is_authorized_to_download, so seeding an account
        # into a group instantly grants it byte access to every active share
        # targeted at that group. Adding members is otherwise admin-only
        # (POST /api/groups/{id}/members), and the send side already refuses an
        # employee targeting a group they don't belong to (GROUP_NOT_MEMBER in
        # share.py::_validate_outbound_targets) - the invite path was the back
        # door around both (audit 2026-07-30). Mirror the same rule here.
        if user.role != UserRole.admin:
            from ..services.share import _user_group_ids

            own_groups = set(_user_group_ids(db, user.id))
            forbidden = [gid for gid in initial_group_ids if gid not in own_groups]
            if forbidden:
                raise AppError(
                    403,
                    "GROUP_NOT_MEMBER",
                    "You can only add invitees to groups you're a member of.",
                    details={"forbidden_group_ids": forbidden},
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
    # Send BEFORE the commit, the same ordering `invite_svc.resend_invite` uses.
    # `_send_resolved` re-raises so the caller sees a send failure, and with the
    # row already durable that left an invite nobody could act on: the plaintext
    # token lived only in this frame, `has_pending_invite` then refused every
    # retry for the full 24h TTL, and resend/revoke are admin-only - so an
    # employee inviter was stuck until an admin intervened (audit 2026-07-30).
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
    db.commit()
    return {"ok": True, "email": record.email, "expires_at": record.expires_at.isoformat()}


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------


@setup_router.get("/2fa/status", response_model=TotpStatusResponse)
def totp_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TotpStatusResponse:
    enabled = totp_svc.is_enabled(user)
    enabled_at = user.totp.enabled_at if user.totp else None
    remaining = (
        db.query(UserRecoveryCode)
        .filter(UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None))
        .count()
    )
    return TotpStatusResponse(enabled=enabled, enabled_at=enabled_at, recovery_codes_remaining=remaining)


@setup_router.post("/2fa/setup", response_model=TotpSetupResponse)
def totp_setup(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TotpSetupResponse:
    payload = totp_svc.begin_setup(db, user=user, request=request)
    db.commit()
    return TotpSetupResponse(**payload)


@setup_router.post("/2fa/enable", response_model=RecoveryCodesResponse)
def totp_enable(
    payload: TotpEnableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    codes = totp_svc.confirm_enable(db, user=user, code=payload.code, request=request)
    db.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


@setup_router.post("/2fa/disable", status_code=status.HTTP_200_OK)
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


@setup_router.post("/2fa/recovery-codes/regenerate", response_model=RecoveryCodesResponse)
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
    # Re-auth: a token outlives the session that minted it and is not revoked by
    # password reset or "sign out other sessions", so it must cost more than a
    # borrowed access token.
    from ..services.step_up import verify_password_or_403

    verify_password_or_403(db, user, payload.password, request=request)
    expires_at = api_token_svc.normalize_expiry(payload.expires_at)
    scopes = api_token_svc.normalize_scopes(payload.scopes)
    record, plaintext = api_token_svc.create_token(
        db, owner=user, name=payload.name, expires_at=expires_at, scopes=scopes
    )

    from ..models.audit_log import AuditEventType
    from ..services.audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.api_token_created,
        actor_user_id=user.id,
        target_type="api_token",
        target_id=record.id,
        metadata={"name": record.name, "scopes": record.scopes_list},
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
        scopes=record.scopes_list,
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
                scopes=r.scopes_list,
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
        scopes=record.scopes_list,
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
