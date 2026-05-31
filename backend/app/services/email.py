"""Email rendering + sending. Templates live under app/templates/email/{locale}.

Phase 1a: text-only stubs for verify, reset_password, invite.
Phase 6a: HTML variants, locale-aware date formatting, the
`render_email` / `enqueue_email_send` pair the notification dispatcher
calls into, plus a shared `subjects.json` per locale so subject lines
live in i18n alongside the bodies.

Direct senders (verify / reset / invite / lockout) stay synchronous —
the auth flow doesn't depend on a worker being up — but the new
notification flows enqueue via the ARQ `send_email_job` task.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from babel.dates import format_datetime
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models.user import Locale
from ..utils.emailing import SmtpConfig, send_email

DEFAULT_TIMEZONE = "UTC"

logger = logging.getLogger("fileheron.email")

_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates" / "email"

# Templates are named `<slug>.<kind>.j2` (e.g. `share_created.html.j2`),
# so the final extension is always `.j2`. `select_autoescape(["html"])`
# keys on the *trailing* extension and would therefore NEVER autoescape —
# leaving user-controlled fields (subject, message, display_name, filename)
# injected raw into HTML mail. Match on the compound `.html.j2` extension
# (and explicitly leave `.txt.j2` un-escaped — plain text needs raw output).
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_ROOT)),
    autoescape=select_autoescape(
        enabled_extensions=("html.j2",),
        disabled_extensions=("txt.j2",),
        default_for_string=False,
    ),
    keep_trailing_newline=True,
)

# Locale-keyed subject lines. Loaded once at import time.
_SUBJECTS: dict[str, dict[str, str]] = {}
for _code in ("en", "de"):
    _path = _TEMPLATE_ROOT / _code / "subjects.json"
    if _path.is_file():
        _SUBJECTS[_code] = json.loads(_path.read_text(encoding="utf-8"))


def _resolve_locale(locale: Locale | str) -> str:
    code = locale.value if isinstance(locale, Locale) else locale
    return code if code in {"en", "de"} else "en"


@pass_context
def _format_dt_locale(jctx, value, locale_code: str = "en") -> str:
    """Jinja filter: format a datetime in the recipient's locale, in the
    admin-set site timezone. Reads ``site_timezone`` from the rendering
    context (set in ``_render``); falls back to UTC if absent or invalid.

    The codebase convention is naive UTC datetimes — we promote naive
    values to aware UTC before handing to babel, then babel re-renders
    in the target tz."""
    if value is None:
        return ""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz_name = jctx.get("site_timezone") or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = DEFAULT_TIMEZONE
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    locale_str = "de_AT" if locale_code == "de" else "en_US"
    rendered = format_datetime(dt, format="medium", locale=locale_str, tzinfo=tz)
    return f"{rendered} ({tz_name})"


_env.filters["dt_locale"] = _format_dt_locale


def _render(
    locale: Locale | str, slug: str, kind: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
) -> str:
    """Render a single template. `kind` is 'txt' or 'html'.

    ``app_url``: explicit override from the caller (db-resolved via
    ``services.site.get_site_url``). Falls back to env when not provided.

    ``site_timezone``: db-resolved IANA name (via
    ``services.site.get_site_timezone``). Falls back to UTC when not
    provided — safe default for tests + auth paths that haven't been
    threaded through yet."""
    code = _resolve_locale(locale)
    candidate = f"{code}/{slug}.{kind}.j2"
    fallback = f"en/{slug}.{kind}.j2"
    try:
        template = _env.get_template(candidate)
    except Exception:
        template = _env.get_template(fallback)
    return template.render(
        **ctx,
        app_name=settings.APP_NAME,
        app_url=app_url if app_url is not None else settings.APP_URL,
        locale=code,
        site_timezone=site_timezone or DEFAULT_TIMEZONE,
    )


def _resolve_subject(locale_code: str, slug: str, ctx: dict) -> str:
    """Pull the subject line from subjects.json with str.format(**ctx).
    Falls back to EN if missing in the requested locale."""
    book = _SUBJECTS.get(locale_code) or {}
    template = book.get(slug)
    if template is None:
        template = (_SUBJECTS.get("en") or {}).get(slug, slug)
    try:
        return template.format(**ctx, app_name=settings.APP_NAME)
    except (KeyError, IndexError):
        return template


def render_email(
    locale: Locale | str, slug: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
) -> tuple[str, str, str | None]:
    """Render (subject, text, html). HTML may be None if no .html.j2 exists.
    Used by the notification dispatcher before enqueueing.

    ``app_url`` should be the kv-resolved value from
    ``services.site.get_site_url``; ``site_timezone`` from
    ``services.site.get_site_timezone``. Both default safely when omitted."""
    code = _resolve_locale(locale)
    subject = _resolve_subject(code, slug, ctx)
    text = _render(code, slug, "txt", ctx, app_url=app_url, site_timezone=site_timezone)
    try:
        html = _render(code, slug, "html", ctx, app_url=app_url, site_timezone=site_timezone)
    except Exception:
        html = None
    return subject, text, html


# -------------------------------------------------------------------------
# SMTP config resolution (post-Phase 10 — admin-editable in app_settings,
# falls back to env vars for unmodified deploys).
# -------------------------------------------------------------------------


def resolve_smtp_config(db: Session) -> SmtpConfig:
    """DB-overlay-env: each field comes from `app_settings` if set,
    otherwise the corresponding `settings.SMTP_*` env value.

    `tls_mode` defaults to the legacy port-based heuristic
    (port 465 → implicit, otherwise → starttls) for backwards-
    compat with deploys that had no explicit setting.
    """
    from . import settings as settings_svc

    def _eff(key: str, env_val: str) -> str:
        v = settings_svc.get(db, key)
        return v if v is not None else env_val

    port_str = _eff(settings_svc.Keys.SMTP_PORT, str(settings.SMTP_PORT))
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = settings.SMTP_PORT

    tls_mode = _eff(settings_svc.Keys.SMTP_TLS_MODE, "") or ""
    if tls_mode not in ("implicit", "starttls", "none"):
        tls_mode = "implicit" if port == 465 else "starttls"

    return SmtpConfig(
        host=_eff(settings_svc.Keys.SMTP_HOST, settings.SMTP_HOST),
        port=port,
        user=_eff(settings_svc.Keys.SMTP_USER, settings.SMTP_USER),
        password=_eff(settings_svc.Keys.SMTP_PASSWORD, settings.SMTP_PASSWORD),
        from_email=_eff(settings_svc.Keys.SMTP_FROM_EMAIL, settings.SMTP_FROM_EMAIL),
        from_name=_eff(settings_svc.Keys.SMTP_FROM_NAME, settings.SMTP_FROM_NAME),
        tls_mode=tls_mode,
    )


async def _send_resolved(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Helper for the direct senders below. Opens its own short-lived
    session, resolves SMTP config from DB-overlay-env, then sends.
    Keeps callers free of DB plumbing for one-off transactional sends.
    """
    db = SessionLocal()
    try:
        cfg = resolve_smtp_config(db)
    finally:
        db.close()
    await send_email(
        cfg=cfg, to=to, subject=subject, text_body=text_body, html_body=html_body
    )


# -------------------------------------------------------------------------
# Test-send (admin diagnostic — synchronous, never queued)
# -------------------------------------------------------------------------


def _smtp_error_payload(exc: Exception) -> dict:
    """Convert any aiosmtplib / OSError exception into the structured
    diagnostic the admin UI renders. Pulls SMTP code if available."""
    out: dict = {
        "ok": False,
        "error_class": type(exc).__name__,
        "error_message": str(exc) or repr(exc),
        "smtp_code": None,
    }
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        out["smtp_code"] = code
    return out


async def test_send(
    db: Session,
    *,
    to: str,
    override: SmtpConfig | None = None,
) -> dict:
    """Synchronously attempt one test email through `aiosmtplib`. If
    `override` is supplied (admin testing unsaved values) it's used
    instead of the persisted config. Returns a structured diagnostic
    that the SPA renders inline."""
    cfg = override or resolve_smtp_config(db)
    if not cfg.is_configured:
        return {
            "ok": False,
            "error_class": "NotConfigured",
            "error_message": "SMTP host is empty. The dev logs-fallback would be used.",
            "smtp_code": None,
        }
    from . import site as site_svc

    ctx = {
        "now_iso": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    }
    locale_code = "en"
    tz = site_svc.get_site_timezone(db)
    subject = _resolve_subject(locale_code, "smtp_test", ctx)
    text = _render(locale_code, "smtp_test", "txt", ctx, site_timezone=tz)
    try:
        html = _render(locale_code, "smtp_test", "html", ctx, site_timezone=tz)
    except Exception:
        html = None
    try:
        await send_email(
            cfg=cfg, to=to, subject=subject, text_body=text, html_body=html
        )
    except Exception as exc:  # noqa: BLE001 — surface every error to the admin
        return _smtp_error_payload(exc)
    return {"ok": True, "error_class": None, "error_message": None, "smtp_code": None}


# -------------------------------------------------------------------------
# Phase 1a-style direct senders (verify / reset / invite / lockout) stay
# in place — they're called from auth flows that should not depend on
# the queue being up. Each opens its own SMTP-config resolution session.
# -------------------------------------------------------------------------


def _app_url(explicit: str | None) -> str:
    """Direct-sender helper: explicit kv-resolved value if the caller
    provided one, else env. Direct senders (verify/reset/invite/lockout)
    accept ``app_url`` + ``site_timezone`` so the auth router can pass
    ``site_svc.get_site_url(db)`` / ``site_svc.get_site_timezone(db)``
    without each one re-opening a session."""
    return explicit if explicit is not None else settings.APP_URL


def _site_tz(explicit: str | None) -> str:
    return explicit if explicit is not None else DEFAULT_TIMEZONE


async def send_verify_email(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    app_url: str | None = None, site_timezone: str | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {"display_name": display_name, "verify_url": f"{base}/verify-email/{token}"}
    body = _render(locale, "verify", "txt", ctx, app_url=base, site_timezone=tz)
    subject = _resolve_subject(_resolve_locale(locale), "verify", ctx)
    await _send_resolved(to=to, subject=subject, text_body=body)


async def send_password_reset_email(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    app_url: str | None = None, site_timezone: str | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {"display_name": display_name, "reset_url": f"{base}/reset-password/{token}"}
    body = _render(locale, "reset_password", "txt", ctx, app_url=base, site_timezone=tz)
    subject = _resolve_subject(_resolve_locale(locale), "reset_password", ctx)
    await _send_resolved(to=to, subject=subject, text_body=body)


async def send_invite_email(
    *, to: str, locale: Locale | str, display_name_hint: str, inviter_display_name: str,
    token: str, app_url: str | None = None, site_timezone: str | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name_hint": display_name_hint,
        "inviter_display_name": inviter_display_name,
        "register_url": f"{base}/register/{token}",
    }
    body = _render(locale, "invite", "txt", ctx, app_url=base, site_timezone=tz)
    subject = _resolve_subject(_resolve_locale(locale), "invite", ctx)
    await _send_resolved(to=to, subject=subject, text_body=body)


async def send_lockout_warning_email(
    *,
    to: str,
    locale: Locale | str,
    display_name: str,
    locked_until_iso: str,
    ip_hint: str | None,
    app_url: str | None = None,
    site_timezone: str | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "locked_until": locked_until_iso,
        "ip_hint": ip_hint or "unknown",
        "reset_url": f"{base}/forgot-password",
    }
    body = _render(locale, "lockout_warning", "txt", ctx, app_url=base, site_timezone=tz)
    subject = _resolve_subject(_resolve_locale(locale), "lockout_warning", ctx)
    await _send_resolved(to=to, subject=subject, text_body=body)
