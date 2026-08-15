"""/api/admin/settings/* - all kv-store admin settings.

Groups: public-link policy, SMTP/email, home page, share defaults, site
URL, 2FA enforcement, quarantine notify-admins toggle.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from sqlalchemy.orm import Session

from ...config import settings as _env_settings
from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.group import Group
from ...models.user import User
from ...schemas.admin import (
    EmailChangePolicyResponse,
    UpdateEmailChangePolicyRequest,
)
from ...schemas.advanced_settings import (
    AdvancedSettingItem,
    AdvancedSettingsResponse,
    UpdateAdvancedSettingsRequest,
)
from ...schemas.branding_settings import (
    BrandingLogoMeta,
    BrandingSettingsResponse,
    UpdateBrandingSettingsRequest,
)
from ...schemas.email_settings import (
    EmailSettingsResponse,
    TestEmailRequest,
    TestEmailResponse,
    UpdateEmailSettingsRequest,
)
from ...schemas.error_alert_settings import (
    ErrorAlertSettingsResponse,
    UpdateErrorAlertSettingsRequest,
)
from ...schemas.file_preview_settings import (
    FilePreviewSettingsResponse,
    UpdateFilePreviewSettingsRequest,
)
from ...schemas.home_page_settings import (
    HomePageSettingsResponse,
    UpdateHomePageSettingsRequest,
)
from ...schemas.legal_settings import (
    LegalDoc,
    LegalSettingsResponse,
    UpdateLegalSettingsRequest,
)
from ...schemas.maintenance import (
    MaintenanceSettingsResponse,
    UpdateMaintenanceSettingsRequest,
)
from ...schemas.motd_settings import (
    MotdSettingsResponse,
    UpdateMotdSettingsRequest,
)
from ...schemas.public_link import (
    PublicLinkAllowedGroup,
    PublicLinkAllowedUser,
    PublicLinkPolicyResponse,
    UpdatePublicLinkPolicyRequest,
)
from ...schemas.quarantine import (
    QuarantineSettingsResponse,
    UpdateQuarantineSettingsRequest,
)
from ...schemas.share_approval_settings import (
    ApproverGroupRef,
    ApproverUserRef,
    ShareApprovalSettingsResponse,
    UpdateShareApprovalSettingsRequest,
)
from ...schemas.share_defaults_settings import (
    ShareDefaultsResponse,
    UpdateShareDefaultsRequest,
)
from ...schemas.site_settings import (
    SiteSettingsResponse,
    UpdateSiteSettingsRequest,
)
from ...schemas.twofa_policy import (
    RequiredGroupRef,
    TwofaPolicyResponse,
    UpdateTwofaPolicyRequest,
)
from ...schemas.updates_settings import (
    UpdatesSettingsResponse,
    UpdateUpdatesSettingsRequest,
)
from ...services import email as email_svc
from ...services import email_change_policy, error_alert, mail_test_gate, richtext, settings_registry
from ...services import public_link as public_link_svc
from ...services import settings as settings_svc
from ...services import share_approval as share_approval_svc
from ...services import site as site_svc
from ...services import twofa_policy as twofa_policy_svc
from ...services.audit import record_audit_event

router = APIRouter()


# ---- Public link policy ----------------------------------------------------


@router.get(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def get_public_link_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    mode, user_ids, group_ids = public_link_svc._resolve_policy(db)
    users = (
        db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    )
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    return PublicLinkPolicyResponse(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            PublicLinkAllowedUser(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                role=u.role.value,
            )
            for u in users
        ],
        allowed_groups=[
            PublicLinkAllowedGroup(id=g.id, name=g.name) for g in groups
        ],
    )


@router.put(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def update_public_link_policy(
    payload: UpdatePublicLinkPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    if payload.allowed_user_ids:
        found_user_ids = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.allowed_user_ids))
            .all()
        }
        missing = [
            uid for uid in payload.allowed_user_ids if uid not in found_user_ids
        ]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.allowed_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.allowed_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.allowed_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_POLICY_MODE,
        value=payload.mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS,
        value=json.dumps(payload.allowed_user_ids) if payload.allowed_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS,
        value=json.dumps(payload.allowed_group_ids) if payload.allowed_group_ids else None,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="public_link_policy",
        metadata={
            "mode": payload.mode,
            "user_count": len(payload.allowed_user_ids),
            "group_count": len(payload.allowed_group_ids),
        },
        request=request,
    )
    db.commit()
    return get_public_link_policy(db=db, _admin=admin)


# ---- Email / SMTP ----------------------------------------------------------


def _to_email_response(db: Session) -> EmailSettingsResponse:
    cfg = email_svc.resolve_smtp_config(db)
    has_overrides = any(
        settings_svc.get(db, k) is not None
        for k in (
            settings_svc.Keys.SMTP_HOST,
            settings_svc.Keys.SMTP_PORT,
            settings_svc.Keys.SMTP_USER,
            settings_svc.Keys.SMTP_PASSWORD,
            settings_svc.Keys.SMTP_FROM_EMAIL,
            settings_svc.Keys.SMTP_FROM_NAME,
            settings_svc.Keys.SMTP_TLS_MODE,
            settings_svc.Keys.SMTP_HELO_HOSTNAME,
        )
    )
    return EmailSettingsResponse(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        is_password_set=bool(cfg.password),
        from_email=cfg.from_email,
        from_name=cfg.from_name,
        tls_mode=cfg.tls_mode,  # type: ignore[arg-type]
        helo_hostname=cfg.helo_hostname,
        is_configured=cfg.is_configured,
        has_db_overrides=has_overrides,
    )


@router.get("/settings/email", response_model=EmailSettingsResponse)
def get_email_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailSettingsResponse:
    return _to_email_response(db)


@router.put("/settings/email", response_model=EmailSettingsResponse)
def update_email_settings(
    payload: UpdateEmailSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailSettingsResponse:
    """Field semantics: missing/None = leave alone; "" = clear; other =
    replace. Same convention as the Phase 9 OIDC PUT.

    Password gets the same special handling: null keeps existing,
    "" clears, anything else replaces (and is encrypted at rest by the
    settings service since `SMTP_PASSWORD` is in `_ENCRYPTED_KEYS`).
    """
    pairs: list[tuple[str, str | int | None]] = [
        (settings_svc.Keys.SMTP_HOST, payload.host),
        (settings_svc.Keys.SMTP_PORT, payload.port),
        (settings_svc.Keys.SMTP_USER, payload.user),
        (settings_svc.Keys.SMTP_FROM_EMAIL, payload.from_email),
        (settings_svc.Keys.SMTP_FROM_NAME, payload.from_name),
        (settings_svc.Keys.SMTP_TLS_MODE, payload.tls_mode),
        (settings_svc.Keys.SMTP_HELO_HOSTNAME, payload.helo_hostname),
    ]
    changed_keys: list[str] = []
    for key, value in pairs:
        if value is None:
            continue
        coerced: str | None
        coerced = str(value) if isinstance(value, int) else value if value else None
        settings_svc.set_value(
            db, key=key, value=coerced, actor=admin, request=request
        )
        changed_keys.append(key)

    if payload.password is not None:
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SMTP_PASSWORD,
            value=payload.password if payload.password else None,
            actor=admin,
            request=request,
        )
        changed_keys.append(settings_svc.Keys.SMTP_PASSWORD)

    if changed_keys:
        record_audit_event(
            db,
            event_type=AuditEventType.smtp_config_changed,
            actor_user_id=admin.id,
            target_type="settings",
            target_id="smtp",
            metadata={"keys": sorted(set(changed_keys))},
            request=request,
        )
    db.commit()
    return _to_email_response(db)


@router.post("/settings/email/test", response_model=TestEmailResponse)
async def test_email_send(
    payload: TestEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TestEmailResponse:
    """Sends a fixed test email synchronously, bypassing the ARQ queue
    so the admin sees the actual SMTP error in real time.

    If `override` is provided in the request, those values are used for
    this one send (no DB write). Otherwise the persisted config is
    used. Password override of None means "use whatever's stored";
    "" means "no auth"; any other value is used directly.
    """
    override = None
    if payload.override is not None:
        from ...utils.emailing import SmtpConfig

        persisted = email_svc.resolve_smtp_config(db)
        ov = payload.override

        def _o(field: str | None, fallback: str) -> str:
            if field is None:
                return fallback
            return field

        port = ov.port if ov.port is not None else persisted.port
        tls_mode = ov.tls_mode if ov.tls_mode is not None else persisted.tls_mode
        password = (
            persisted.password if ov.password is None else ov.password
        )
        host = _o(ov.host, persisted.host)
        user = _o(ov.user, persisted.user)

        # Leaving the password blank means "use the stored one". Combined with a
        # freely chosen host that is a credential-exfiltration primitive, so a
        # stored secret may only travel to the saved server unless the caller
        # re-authenticates. Compared on RESOLVED values, never the raw payload:
        # the SPA sends `port: undefined` while the number input is momentarily
        # empty, which means "keep persisted" and must not read as a mismatch.
        mail_test_gate.guard_and_audit(
            db,
            admin=admin,
            request=request,
            event_type=AuditEventType.smtp_test_foreign_target,
            target_id="smtp",
            confirm_password=payload.confirm_password,
            reuses_stored_secret=ov.password is None,
            target_matches_persisted=(
                host == persisted.host
                and port == persisted.port
                and user == persisted.user
            ),
            host=host,
            port=port,
            tls_mode=tls_mode,
        )

        # The override host is attacker-reachable through a hijacked admin
        # session and this route CONNECTS and reports the result, so it is a
        # non-blind SSRF probe - stronger than the webhook path, which is
        # guarded. Apply the same address policy before the socket opens.
        # NOTE: this is an ADDRESS policy only (allow_private=True, fails open
        # on an unresolvable name). It never mitigated the credential leak
        # above; the gate does.
        from ...utils.net import assert_safe_host

        assert_safe_host(host, port)

        override = SmtpConfig(
            host=host,
            port=port,
            user=user,
            password=password,
            from_email=_o(ov.from_email, persisted.from_email),
            from_name=_o(ov.from_name, persisted.from_name),
            tls_mode=tls_mode,
            helo_hostname=_o(ov.helo_hostname, persisted.helo_hostname),
        )
    result = await email_svc.test_send(db, to=payload.to, override=override)
    return TestEmailResponse(**result)


# ---- Home page -------------------------------------------------------------


@router.get("/settings/home-page", response_model=HomePageSettingsResponse)
def get_home_page_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> HomePageSettingsResponse:
    enabled = settings_svc.get_bool(
        db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True
    )
    return HomePageSettingsResponse(enabled=enabled)


@router.get("/settings/motd", response_model=MotdSettingsResponse)
def get_motd_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> MotdSettingsResponse:
    return MotdSettingsResponse(
        enabled=settings_svc.get_bool(db, settings_svc.Keys.MOTD_ENABLED, default=False),
        text=settings_svc.get(db, settings_svc.Keys.MOTD_TEXT) or "",
    )


_DEFAULT_UPDATES_API_URL = (
    "https://api.github.com/repos/phoen-ix/fileHeron/releases/latest"
)


@router.get("/settings/updates", response_model=UpdatesSettingsResponse)
def get_updates_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> UpdatesSettingsResponse:
    """Admin-editable update-check settings: where to poll + how often."""
    return UpdatesSettingsResponse(
        api_url=settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL)
        or _DEFAULT_UPDATES_API_URL,
    )


@router.put("/settings/updates", response_model=UpdatesSettingsResponse)
def update_updates_settings(
    payload: UpdateUpdatesSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> UpdatesSettingsResponse:
    """Admin-only PUT. URL must be http(s)://; mode is validated by the schema."""
    if not (
        payload.api_url.startswith("http://")
        or payload.api_url.startswith("https://")
    ):
        raise AppError(
            400, "INVALID_URL", "api_url must start with http:// or https://"
        )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.UPDATES_API_URL,
        value=payload.api_url,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.updates_settings_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="updates",
        metadata={"url_changed": True},
        request=request,
    )
    db.commit()
    return UpdatesSettingsResponse(api_url=payload.api_url)


@router.put("/settings/motd", response_model=MotdSettingsResponse)
def update_motd_settings(
    payload: UpdateMotdSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> MotdSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MOTD_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MOTD_TEXT,
        value=payload.text if payload.text else None,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.motd_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="motd",
        metadata={"enabled": payload.enabled, "text_length": len(payload.text)},
        request=request,
    )
    db.commit()
    return MotdSettingsResponse(enabled=payload.enabled, text=payload.text)


@router.put("/settings/home-page", response_model=HomePageSettingsResponse)
def update_home_page_settings(
    payload: UpdateHomePageSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> HomePageSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.HOME_PAGE_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.home_page_toggled,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="home_page",
        metadata={"enabled": payload.enabled},
        request=request,
    )
    db.commit()
    return HomePageSettingsResponse(enabled=payload.enabled)


# ---- File preview (in-browser) ---------------------------------------------


@router.get("/settings/file-preview", response_model=FilePreviewSettingsResponse)
def get_file_preview_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> FilePreviewSettingsResponse:
    return FilePreviewSettingsResponse(
        enabled=settings_svc.get_bool(
            db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
        )
    )


@router.put("/settings/file-preview", response_model=FilePreviewSettingsResponse)
def update_file_preview_settings(
    payload: UpdateFilePreviewSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> FilePreviewSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.FILE_PREVIEW_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.file_preview_toggled,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="file_preview",
        metadata={"enabled": payload.enabled},
        request=request,
    )
    db.commit()
    return FilePreviewSettingsResponse(enabled=payload.enabled)


# ---- Maintenance mode ------------------------------------------------------


def _maintenance_response(db: Session) -> MaintenanceSettingsResponse:
    from ...services import maintenance as maintenance_svc
    from ...services import transfer_activity as ta

    snap = ta.snapshot(db)
    return MaintenanceSettingsResponse(
        enabled=maintenance_svc.is_enabled(db),
        message=maintenance_svc.get_message(db),
        active_uploads=snap["active_uploads"],
        active_downloads=snap["active_downloads"],
    )


@router.get("/settings/maintenance", response_model=MaintenanceSettingsResponse)
def get_maintenance_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> MaintenanceSettingsResponse:
    return _maintenance_response(db)


@router.put("/settings/maintenance", response_model=MaintenanceSettingsResponse)
def update_maintenance_settings(
    payload: UpdateMaintenanceSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> MaintenanceSettingsResponse:
    from ...services import maintenance as maintenance_svc

    # Refuse to switch maintenance OFF from here while an update is postponed.
    # This page and /admin/system share one flag: turning it off here left
    # `maintenance.pending_update` armed, so the minute drain worker still saw a
    # drained stack and restarted it - an unannounced restart during what the
    # admin believed was normal operation, because they thought they had
    # cancelled something (audit 2026-07-30). Cancelling is a different action
    # with a different audit event, so point them at it rather than guessing.
    if not payload.enabled and maintenance_svc.get_pending_update(db) is not None:
        raise AppError(
            409,
            "UPDATE_PENDING",
            "An update is postponed and waiting for transfers to drain. Cancel "
            "it on the System page before leaving maintenance mode.",
        )
    maintenance_svc.set_enabled(
        db, payload.enabled, actor=admin, message=payload.message, request=request
    )
    db.commit()
    return _maintenance_response(db)


# ---- Share defaults --------------------------------------------------------


@router.get("/settings/share-defaults", response_model=ShareDefaultsResponse)
def get_share_defaults_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ShareDefaultsResponse:
    enabled = settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT, default=True
    )
    return ShareDefaultsResponse(notify_recipients_default=enabled)


@router.put("/settings/share-defaults", response_model=ShareDefaultsResponse)
def update_share_defaults_settings(
    payload: UpdateShareDefaultsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ShareDefaultsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT,
        value="true" if payload.notify_recipients_default else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.share_defaults_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="share_defaults",
        metadata={"notify_recipients_default": payload.notify_recipients_default},
        request=request,
    )
    db.commit()
    return ShareDefaultsResponse(
        notify_recipients_default=payload.notify_recipients_default
    )


# ---- Share-approval policy (v1.24.0) ---------------------------------------


def _share_approval_response(db: Session) -> ShareApprovalSettingsResponse:
    mode, user_ids, group_ids = share_approval_svc.resolve_approver_policy(db)
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    return ShareApprovalSettingsResponse(
        enabled=share_approval_svc.is_enabled(db),
        approver_mode=mode,  # type: ignore[arg-type]
        approver_user_ids=user_ids,
        approver_group_ids=group_ids,
        approver_users=[
            ApproverUserRef(
                id=u.id, display_name=u.display_name, email=u.email, role=u.role.value
            )
            for u in users
        ],
        approver_groups=[ApproverGroupRef(id=g.id, name=g.name) for g in groups],
        scope=share_approval_svc.effective_scope(db),  # type: ignore[arg-type]
        exempt_approvers=share_approval_svc.exempt_approvers(db),
        allow_content_review=share_approval_svc.allow_content_review(db),
        is_inert=share_approval_svc.is_inert(db),
    )


@router.get("/settings/share-approval", response_model=ShareApprovalSettingsResponse)
def get_share_approval_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ShareApprovalSettingsResponse:
    return _share_approval_response(db)


@router.put("/settings/share-approval", response_model=ShareApprovalSettingsResponse)
def update_share_approval_settings(
    payload: UpdateShareApprovalSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ShareApprovalSettingsResponse:
    if payload.approver_user_ids:
        found = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.approver_user_ids))
            .all()
        }
        missing = [i for i in payload.approver_user_ids if i not in found]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.approver_group_ids:
        found = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.approver_group_ids))
            .all()
        }
        missing = [i for i in payload.approver_group_ids if i not in found]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    # Refuse a combination that can never queue anything. "Every employee may
    # approve" plus "an approver's own shares are exempt" cancel each other out:
    # staff create outbound shares, every employee is an approver, so every
    # outbound share is exempted at birth and only inbound (client) shares are
    # left - which the outbound scopes exclude. The result is a four-eyes
    # control that is on, looks configured, and stops nothing. Worse than off,
    # because it manufactures assurance (audit 2026-07-30).
    if payload.enabled and share_approval_svc.policy_is_inert(
        payload.approver_mode, payload.scope, payload.exempt_approvers
    ):
        raise AppError(
            400,
            "APPROVAL_POLICY_INERT",
            "This combination means no share can ever require approval: every "
            "employee is an approver, and approvers' own shares are exempt. "
            "Set the approver mode to admins only, turn off the approver "
            "exemption, or widen the scope to all shares.",
            details={
                "approver_mode": payload.approver_mode,
                "scope": payload.scope,
                "exempt_approvers": payload.exempt_approvers,
            },
        )

    keys = settings_svc.Keys
    settings_svc.set_value(
        db, key=keys.SHARE_APPROVAL_ENABLED, value="true" if payload.enabled else "false", actor=admin
    )
    settings_svc.set_value(db, key=keys.SHARE_APPROVAL_APPROVER_MODE, value=payload.approver_mode, actor=admin)
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_APPROVER_USERS,
        value=json.dumps(payload.approver_user_ids) if payload.approver_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_APPROVER_GROUPS,
        value=json.dumps(payload.approver_group_ids) if payload.approver_group_ids else None,
        actor=admin,
    )
    settings_svc.set_value(db, key=keys.SHARE_APPROVAL_SCOPE, value=payload.scope, actor=admin)
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_EXEMPT_APPROVERS,
        value="true" if payload.exempt_approvers else "false",
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW,
        value="true" if payload.allow_content_review else "false",
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.share_approval_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="share_approval",
        metadata={
            "enabled": payload.enabled,
            "mode": payload.approver_mode,
            "scope": payload.scope,
            "user_count": len(payload.approver_user_ids),
            "group_count": len(payload.approver_group_ids),
            "exempt_approvers": payload.exempt_approvers,
            "allow_content_review": payload.allow_content_review,
        },
        request=request,
    )
    db.commit()
    return _share_approval_response(db)


# ---- Email-change policy ---------------------------------------------------


def _email_change_policy_response(db: Session) -> EmailChangePolicyResponse:
    return EmailChangePolicyResponse(
        verification_mode=email_change_policy.effective_verification_mode(db),
        self_service=email_change_policy.self_service_enabled(db),
        oidc_mode=email_change_policy.effective_oidc_mode(db),
    )


@router.get("/settings/email-change", response_model=EmailChangePolicyResponse)
def get_email_change_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailChangePolicyResponse:
    return _email_change_policy_response(db)


@router.put("/settings/email-change", response_model=EmailChangePolicyResponse)
def update_email_change_policy(
    payload: UpdateEmailChangePolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailChangePolicyResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_VERIFICATION_MODE,
        value=payload.verification_mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE,
        value="true" if payload.self_service else "false",
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_OIDC_MODE,
        value=payload.oidc_mode,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.email_change_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="email_change",
        metadata={
            "verification_mode": payload.verification_mode,
            "self_service": payload.self_service,
            "oidc_mode": payload.oidc_mode,
        },
        request=request,
    )
    db.commit()
    return _email_change_policy_response(db)


# ---- Site URL --------------------------------------------------------------


def _site_settings_response(db: Session) -> SiteSettingsResponse:
    override = settings_svc.get(db, settings_svc.Keys.SITE_URL)
    return SiteSettingsResponse(
        site_url=site_svc.get_site_url(db),
        has_db_override=override is not None,
        env_app_url=(_env_settings.APP_URL or "").rstrip("/"),
        site_timezone=site_svc.get_site_timezone(db),
    )


@router.get("/settings/site", response_model=SiteSettingsResponse)
def get_site_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> SiteSettingsResponse:
    return _site_settings_response(db)


@router.put("/settings/site", response_model=SiteSettingsResponse)
def update_site_settings(
    payload: UpdateSiteSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> SiteSettingsResponse:
    fields = payload.model_fields_set
    if "site_url" in fields:
        previous_url = site_svc.get_site_url(db)
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SITE_URL,
            value=payload.site_url,  # None clears the kv override
            actor=admin,
            request=request,
        )
        db.flush()
        new_url = site_svc.get_site_url(db)
        if new_url != previous_url:
            record_audit_event(
                db,
                event_type=AuditEventType.site_url_changed,
                actor_user_id=admin.id,
                target_type="settings",
                target_id="site_url",
                metadata={"from": previous_url, "to": new_url},
                request=request,
            )
    if "site_timezone" in fields:
        previous_tz = site_svc.get_site_timezone(db)
        # Validator turns "" into "" (clear sentinel); store None to drop
        # the row so future reads fall through to the default helper.
        write_value: str | None = payload.site_timezone or None
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SITE_TIMEZONE,
            value=write_value,
            actor=admin,
            request=request,
        )
        db.flush()
        new_tz = site_svc.get_site_timezone(db)
        if new_tz != previous_tz:
            record_audit_event(
                db,
                event_type=AuditEventType.site_timezone_changed,
                actor_user_id=admin.id,
                target_type="settings",
                target_id="site_timezone",
                metadata={"from": previous_tz, "to": new_tz},
                request=request,
            )
    db.commit()
    return _site_settings_response(db)


# ---- 2FA enforcement -------------------------------------------------------


def _twofa_policy_response(db: Session) -> TwofaPolicyResponse:
    roles, group_ids, is_kv_overridden = twofa_policy_svc._resolve_policy(db)
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    by_id = {g.id: g for g in groups}
    return TwofaPolicyResponse(
        required_roles=sorted(roles),
        required_group_ids=group_ids,
        required_groups=[
            RequiredGroupRef(
                id=g.id,
                name=g.name,
                is_company_inbox=getattr(g, "is_company_inbox", False),
            )
            for gid in group_ids
            if (g := by_id.get(gid)) is not None
        ],
        is_kv_overridden=is_kv_overridden,
    )


@router.get("/settings/twofa", response_model=TwofaPolicyResponse)
def get_twofa_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    return _twofa_policy_response(db)


@router.put("/settings/twofa", response_model=TwofaPolicyResponse)
def update_twofa_policy(
    payload: UpdateTwofaPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    bad_roles = [
        r for r in payload.required_roles if r not in twofa_policy_svc.ALLOWED_ROLES
    ]
    if bad_roles:
        raise AppError(
            400,
            "INVALID_ROLE",
            "One or more role names are not recognised.",
            details={"invalid_roles": bad_roles},
        )

    if payload.required_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.required_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.required_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    twofa_policy_svc.write_policy(
        db,
        actor=admin,
        required_roles=payload.required_roles,
        required_group_ids=payload.required_group_ids,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.twofa_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="twofa_policy",
        metadata={
            "role_count": len(set(payload.required_roles)),
            "group_count": len(set(payload.required_group_ids)),
        },
        request=request,
    )
    db.commit()
    return _twofa_policy_response(db)


# ---- Quarantine notify-admins toggle --------------------------------------


@router.get("/settings/quarantine", response_model=QuarantineSettingsResponse)
def get_quarantine_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    return QuarantineSettingsResponse(
        notify_admins=settings_svc.get_bool(
            db, settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, default=False
        )
    )


@router.put("/settings/quarantine", response_model=QuarantineSettingsResponse)
def update_quarantine_settings(
    payload: UpdateQuarantineSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS,
        value="true" if payload.notify_admins else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.quarantine_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="quarantine",
        metadata={"notify_admins": payload.notify_admins},
        request=request,
    )
    db.commit()
    return QuarantineSettingsResponse(notify_admins=payload.notify_admins)


# ---- Error alerts (email admins on server errors) -------------------------


@router.get("/settings/error-alerts", response_model=ErrorAlertSettingsResponse)
def get_error_alert_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ErrorAlertSettingsResponse:
    return ErrorAlertSettingsResponse(**error_alert.get_settings(db))


@router.put("/settings/error-alerts", response_model=ErrorAlertSettingsResponse)
def update_error_alert_settings(
    payload: UpdateErrorAlertSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ErrorAlertSettingsResponse:
    result = error_alert.update_settings(
        db,
        enabled=payload.enabled,
        source_http_5xx=payload.source_http_5xx,
        source_http_4xx=payload.source_http_4xx,
        recipients_mode=payload.recipients_mode,
        custom_recipients=payload.custom_recipients,
        cooldown_minutes=payload.cooldown_minutes,
        max_per_hour=payload.max_per_hour,
        log_enabled=payload.log_enabled,
        capture_4xx=payload.capture_4xx,
        http_4xx_codes=payload.http_4xx_codes,
        retention_days=payload.retention_days,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.error_alert_settings_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="error_alerts",
        # Record counts/keys only - never the recipient addresses themselves.
        metadata={
            "enabled": payload.enabled,
            "source_http_5xx": payload.source_http_5xx,
            "source_http_4xx": payload.source_http_4xx,
            "recipients_mode": payload.recipients_mode,
            "recipient_count": len(payload.custom_recipients),
            "cooldown_minutes": payload.cooldown_minutes,
            "max_per_hour": payload.max_per_hour,
            "log_enabled": payload.log_enabled,
            "capture_4xx": payload.capture_4xx,
            "http_4xx_code_count": len(payload.http_4xx_codes),
            "retention_days": payload.retention_days,
        },
        request=request,
    )
    db.commit()
    return ErrorAlertSettingsResponse(**result)


# ---------------------------------------------------------------------------
# Generic registry-driven "Advanced settings" - one GET/PUT for every
# runtime-tunable knob in services/settings_registry.py. Only keys present
# in the registry are ever exposed or accepted (secrets/infra stay env-only).
# ---------------------------------------------------------------------------


@router.get("/settings/advanced", response_model=AdvancedSettingsResponse)
def get_advanced_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdvancedSettingsResponse:
    items: list[AdvancedSettingItem] = []
    for spec in settings_registry.TUNABLES:
        items.append(
            AdvancedSettingItem(
                key=spec.key,
                group=spec.group,
                kind=spec.kind,
                value=settings_registry.effective(db, spec.key),
                default=settings_registry.env_default(spec),
                is_overridden=settings_svc.get(db, spec.key) is not None,
                min=spec.min,
                max=spec.max,
            )
        )
    return AdvancedSettingsResponse(items=items)


@router.put("/settings/advanced", response_model=AdvancedSettingsResponse)
def update_advanced_settings(
    payload: UpdateAdvancedSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdvancedSettingsResponse:
    """Set or reset registry knobs. `null` resets a key to its env default.
    Unknown keys and out-of-bounds/typed-wrong values are rejected (400)
    before any write, so the PUT is all-or-nothing."""
    # Validate everything first (atomic - reject the whole PUT on any error).
    to_set: dict[str, str | None] = {}
    for key, value in payload.updates.items():
        spec = settings_registry.BY_KEY.get(key)
        if spec is None:
            raise AppError(400, "UNKNOWN_SETTING", f"Unknown setting: {key}")
        if value is None:
            to_set[key] = None  # reset to env default
            continue
        try:
            to_set[key] = settings_registry.coerce_for_store(spec, value)
        except ValueError as e:
            raise AppError(400, "INVALID_SETTING", str(e)) from None

    if not to_set:
        return get_advanced_settings(db=db, _admin=admin)

    # Capture the pre-write refresh-TTL so we can detect a *shortening* and
    # apply it to existing sessions (clamp down; revoke only ones already
    # expired under the new value).
    refresh_ttl_old = settings_registry.effective(
        db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
    )

    for key, stored in to_set.items():
        settings_svc.set_value(db, key=key, value=stored, actor=admin, request=request)
    settings_svc.audit_settings_change(
        db, actor=admin, changed_keys=to_set.keys(), request=request
    )

    if settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS in to_set:
        refresh_ttl_new = settings_registry.effective(
            db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
        )
        if refresh_ttl_new < refresh_ttl_old:
            from ...services import jwt_session
            jwt_session.reclamp_refresh_expiry(
                db, new_days=refresh_ttl_new, actor=admin, request=request
            )

    db.commit()
    return get_advanced_settings(db=db, _admin=admin)


# ---- Branding (logo + surfaces + link) -------------------------------------

_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB


def _sniff_image_type(head: bytes) -> str | None:
    """Return the canonical content-type from magic bytes, or None if the bytes
    aren't an allowed raster image. Don't trust the client-declared type."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _store_client_png(backend, content: bytes) -> str | None:
    """Transcode the logo to a header-sized PNG and store it via the backend.
    Returns the locator, or None if transcoding/storing fails (non-fatal)."""
    import os
    import tempfile
    import uuid
    from pathlib import Path

    from ...config import settings as _cfg
    from ...services import image as image_svc

    try:
        png = image_svc.to_client_png(content)
    except Exception:
        return None
    locator = backend.generate_locator(f"branding-logo-png-{uuid.uuid4().hex}")
    Path(_cfg.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_cfg.TUS_UPLOAD_DIR, suffix=".png")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(png)
        backend.finalize(tmp_path, locator)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None
    return locator


def _branding_response(db: Session) -> BrandingSettingsResponse:
    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    present = bool(locator)
    return BrandingSettingsResponse(
        logo=BrandingLogoMeta(
            present=present,
            filename=settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_FILENAME),
            content_type=settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE),
            url="/api/branding/logo" if present else None,
        ),
        show_header=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_HEADER, default=False),
        show_login=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_LOGIN, default=False),
        show_public=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_PUBLIC, default=False),
        show_email=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_EMAIL, default=False),
        show_client=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_CLIENT, default=False),
        link_url=settings_svc.get(db, settings_svc.Keys.BRANDING_LINK_URL) or None,
    )


@router.get("/settings/branding", response_model=BrandingSettingsResponse)
def get_branding_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    return _branding_response(db)


@router.put("/settings/branding", response_model=BrandingSettingsResponse)
def update_branding_settings(
    payload: UpdateBrandingSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    fields = payload.model_fields_set
    bool_map = {
        "show_header": settings_svc.Keys.BRANDING_SHOW_HEADER,
        "show_login": settings_svc.Keys.BRANDING_SHOW_LOGIN,
        "show_public": settings_svc.Keys.BRANDING_SHOW_PUBLIC,
        "show_email": settings_svc.Keys.BRANDING_SHOW_EMAIL,
        "show_client": settings_svc.Keys.BRANDING_SHOW_CLIENT,
    }
    for attr, key in bool_map.items():
        if attr in fields:
            settings_svc.set_value(
                db, key=key, value="true" if getattr(payload, attr) else "false",
                actor=admin, request=request,
            )
    if "link_url" in fields:
        # Validator turns "" into the clear sentinel; store None to drop the row.
        settings_svc.set_value(
            db, key=settings_svc.Keys.BRANDING_LINK_URL,
            value=(payload.link_url or None), actor=admin, request=request,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.branding_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="branding",
        metadata={"keys": sorted(fields)},
        request=request,
    )
    db.commit()
    return _branding_response(db)


@router.post(
    "/settings/branding/logo",
    response_model=BrandingSettingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_branding_logo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    import os
    import tempfile
    import uuid

    from ...config import settings as _cfg
    from ...services.storage_backend import get_storage_backend

    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > _LOGO_MAX_BYTES:
            raise AppError(413, "LOGO_TOO_LARGE", "Logo must be 2 MB or smaller.")
        chunks.append(chunk)
    content = b"".join(chunks)
    content_type = _sniff_image_type(content[:16])
    if content_type is None:
        raise AppError(415, "INVALID_LOGO_TYPE", "Logo must be a PNG, JPEG, or WebP image.")

    backend = get_storage_backend()
    locator = backend.generate_locator(f"branding-logo-{uuid.uuid4().hex}")
    from pathlib import Path
    Path(_cfg.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_cfg.TUS_UPLOAD_DIR, suffix=".logo")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        backend.finalize(tmp_path, locator)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Header-sized PNG rendition for the desktop client (best-effort - a
    # transcode failure must not block the upload; web/email use the original).
    png_locator = _store_client_png(backend, content)

    previous = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    previous_png = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_LOGO_LOCATOR, value=locator, actor=admin, request=request)
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_FILENAME,
        value=(file.filename or "logo")[:255], actor=admin, request=request,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE,
        value=content_type, actor=admin, request=request,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
        value=png_locator, actor=admin, request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.branding_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="branding_logo",
        metadata={"action": "upload", "content_type": content_type, "size_bytes": received},
        request=request,
    )
    db.commit()
    # Best-effort clean-up of the replaced bytes (after commit so a delete
    # failure can't roll back the new pointer).
    for old in (previous if previous != locator else None, previous_png if previous_png != png_locator else None):
        if old:
            try:
                backend.delete(old)
            except Exception:
                pass
    return _branding_response(db)


@router.delete("/settings/branding/logo", response_model=BrandingSettingsResponse)
def delete_branding_logo(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    from ...services.storage_backend import get_storage_backend

    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    png_locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    for key in (
        settings_svc.Keys.BRANDING_LOGO_LOCATOR,
        settings_svc.Keys.BRANDING_LOGO_FILENAME,
        settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE,
        settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
    ):
        settings_svc.set_value(db, key=key, value=None, actor=admin, request=request)
    if locator:
        record_audit_event(
            db,
            event_type=AuditEventType.branding_changed,
            actor_user_id=admin.id,
            target_type="settings",
            target_id="branding_logo",
            metadata={"action": "delete"},
            request=request,
        )
    db.commit()
    backend = get_storage_backend()
    for loc in (locator, png_locator):
        if loc:
            try:
                backend.delete(loc)
            except Exception:
                pass
    return _branding_response(db)


# ---- Legal pages (imprint + privacy) ---------------------------------------


def _legal_doc(db: Session, enabled_key: str, en_key: str, de_key: str) -> LegalDoc:
    return LegalDoc(
        enabled=settings_svc.get_bool(db, enabled_key, default=False),
        en=settings_svc.get(db, en_key) or "",
        de=settings_svc.get(db, de_key) or "",
    )


def _legal_response(db: Session) -> LegalSettingsResponse:
    keys = settings_svc.Keys
    return LegalSettingsResponse(
        imprint=_legal_doc(
            db, keys.LEGAL_IMPRINT_ENABLED, keys.LEGAL_IMPRINT_EN, keys.LEGAL_IMPRINT_DE
        ),
        privacy=_legal_doc(
            db, keys.LEGAL_PRIVACY_ENABLED, keys.LEGAL_PRIVACY_EN, keys.LEGAL_PRIVACY_DE
        ),
    )


@router.get("/settings/legal", response_model=LegalSettingsResponse)
def get_legal_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> LegalSettingsResponse:
    return _legal_response(db)


@router.put("/settings/legal", response_model=LegalSettingsResponse)
def update_legal_settings(
    payload: UpdateLegalSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> LegalSettingsResponse:
    keys = settings_svc.Keys
    plan = [
        (payload.imprint, keys.LEGAL_IMPRINT_ENABLED, keys.LEGAL_IMPRINT_EN, keys.LEGAL_IMPRINT_DE),
        (payload.privacy, keys.LEGAL_PRIVACY_ENABLED, keys.LEGAL_PRIVACY_EN, keys.LEGAL_PRIVACY_DE),
    ]
    for doc, enabled_key, en_key, de_key in plan:
        settings_svc.set_value(
            db, key=enabled_key, value="true" if doc.enabled else "false",
            actor=admin, request=request,
        )
        # Authored as HTML by the editor; sanitise to the safe allowlist on the
        # way in so stored content is never trusted raw.
        settings_svc.set_value(
            db, key=en_key, value=(richtext.sanitize_html(doc.en) or None),
            actor=admin, request=request,
        )
        settings_svc.set_value(
            db, key=de_key, value=(richtext.sanitize_html(doc.de) or None),
            actor=admin, request=request,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.legal_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="legal",
        metadata={
            "imprint_enabled": payload.imprint.enabled,
            "privacy_enabled": payload.privacy.enabled,
        },
        request=request,
    )
    db.commit()
    return _legal_response(db)
