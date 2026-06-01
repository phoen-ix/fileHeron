"""/api/admin/settings/* — all kv-store admin settings.

Groups: public-link policy, SMTP/email, home page, share defaults, site
URL, 2FA enforcement, quarantine notify-admins toggle.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...config import settings as _env_settings
from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.group import Group
from ...models.user import User
from ...schemas.email_settings import (
    EmailSettingsResponse,
    TestEmailRequest,
    TestEmailResponse,
    UpdateEmailSettingsRequest,
)
from ...schemas.home_page_settings import (
    HomePageSettingsResponse,
    UpdateHomePageSettingsRequest,
)
from ...schemas.motd_settings import (
    MotdSettingsResponse,
    UpdateMotdSettingsRequest,
)
from ...schemas.updates_settings import (
    UpdateUpdatesSettingsRequest,
    UpdatesSettingsResponse,
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
from ...services import email as email_svc
from ...services import public_link as public_link_svc
from ...services import settings as settings_svc
from ...services import settings_registry
from ...services import site as site_svc
from ...services import twofa_policy as twofa_policy_svc
from ...services.audit import record_audit_event
from ...schemas.advanced_settings import (
    AdvancedSettingItem,
    AdvancedSettingsResponse,
    UpdateAdvancedSettingsRequest,
)

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
    ]
    changed_keys: list[str] = []
    for key, value in pairs:
        if value is None:
            continue
        coerced: str | None
        if isinstance(value, int):
            coerced = str(value)
        else:
            coerced = value if value else None
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
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
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

        override = SmtpConfig(
            host=_o(ov.host, persisted.host),
            port=port,
            user=_o(ov.user, persisted.user),
            password=password,
            from_email=_o(ov.from_email, persisted.from_email),
            from_name=_o(ov.from_name, persisted.from_name),
            tls_mode=tls_mode,
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
        check_mode=(
            settings_svc.get(db, settings_svc.Keys.UPDATES_CHECK_MODE) or "auto"
        ),  # type: ignore[arg-type]
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
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.UPDATES_CHECK_MODE,
        value=payload.check_mode,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.updates_settings_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="updates",
        metadata={"check_mode": payload.check_mode, "url_changed": True},
        request=request,
    )
    db.commit()
    return UpdatesSettingsResponse(
        api_url=payload.api_url, check_mode=payload.check_mode
    )


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


# ---------------------------------------------------------------------------
# Generic registry-driven "Advanced settings" — one GET/PUT for every
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
    # Validate everything first (atomic — reject the whole PUT on any error).
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
