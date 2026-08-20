"""SMTP settings and the admin test-send.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.email_settings import (
    EmailSettingsResponse,
    TestEmailRequest,
    TestEmailResponse,
    UpdateEmailSettingsRequest,
)
from ....services import email as email_svc
from ....services import mail_test_gate
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


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
        from ....utils.emailing import SmtpConfig

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
        from ....utils.net import assert_safe_host

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
