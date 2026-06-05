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

from babel.dates import format_date, format_time
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
    # 24-hour time for every locale (convention), with the locale's own date
    # style. en_US `medium` is 12-hour (AM/PM); forcing `HH:mm:ss` keeps it
    # consistent with de_AT (already 24-hour) and the rest of the app.
    local = dt.astimezone(tz)
    rendered = (
        f"{format_date(local, format='medium', locale=locale_str)}, "
        f"{format_time(local, format='HH:mm:ss', locale=locale_str)}"
    )
    return f"{rendered} ({tz_name})"


_env.filters["dt_locale"] = _format_dt_locale


def _render(
    locale: Locale | str, slug: str, kind: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
    app_name: str | None = None,
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
        app_name=app_name if app_name else settings.APP_NAME,
        app_url=app_url if app_url is not None else settings.APP_URL,
        locale=code,
        site_timezone=site_timezone or DEFAULT_TIMEZONE,
    )


def _resolve_subject(
    locale_code: str, slug: str, ctx: dict, app_name: str | None = None
) -> str:
    """Pull the subject line from subjects.json with str.format(**ctx).
    Falls back to EN if missing in the requested locale."""
    book = _SUBJECTS.get(locale_code) or {}
    template = book.get(slug)
    if template is None:
        template = (_SUBJECTS.get("en") or {}).get(slug, slug)
    try:
        return template.format(**ctx, app_name=app_name or settings.APP_NAME)
    except (KeyError, IndexError):
        return template


def render_email(
    locale: Locale | str, slug: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
    app_name: str | None = None,
) -> tuple[str, str, str | None]:
    """Render (subject, text, html). HTML may be None if no .html.j2 exists.
    Used by the notification dispatcher before enqueueing.

    ``app_url`` should be the kv-resolved value from
    ``services.site.get_site_url``; ``site_timezone`` from
    ``services.site.get_site_timezone``; ``app_name`` from
    ``services.site.get_app_name``. All default safely when omitted."""
    code = _resolve_locale(locale)
    subject = _resolve_subject(code, slug, ctx, app_name)
    text = _render(code, slug, "txt", ctx, app_url=app_url, site_timezone=site_timezone, app_name=app_name)
    try:
        html = _render(code, slug, "html", ctx, app_url=app_url, site_timezone=site_timezone, app_name=app_name)
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
        helo_hostname=_eff(settings_svc.Keys.SMTP_HELO_HOSTNAME, settings.SMTP_HELO_HOST),
    )


async def _send_resolved(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    category: str | None = None,
) -> None:
    """Helper for the direct senders below. Opens its own short-lived
    session, resolves SMTP config from DB-overlay-env, then sends.
    Keeps callers free of DB plumbing for one-off transactional sends.

    When ``category`` is supplied, records the send to the mail log
    (best-effort, masked at rest) and re-raises any send error so the
    auth flow still sees the failure. The recipient user is resolved by
    email lookup so direct sends (reset / invite / lockout) still link to
    a user in the admin 'Emails to this user' panel."""
    db = SessionLocal()
    try:
        cfg = resolve_smtp_config(db)
    finally:
        db.close()

    err: Exception | None = None
    try:
        await send_email(
            cfg=cfg, to=to, subject=subject, text_body=text_body, html_body=html_body
        )
    except Exception as exc:  # noqa: BLE001 — re-raised below after logging
        err = exc

    if category is not None:
        from ..models.email_log import EmailStatus, EmailVia
        from ..models.user import User
        from . import mail_log

        if err is None:
            status = EmailStatus.sent
            via = EmailVia.direct if cfg.is_configured else EmailVia.dev_fallback
            smtp_code = error_class = error_message = None
        else:
            status = EmailStatus.failed
            via = EmailVia.direct
            code = getattr(err, "code", None)
            smtp_code = code if isinstance(code, int) else None
            error_class = type(err).__name__
            error_message = str(err)
        log_db = SessionLocal()
        try:
            ruid = log_db.query(User.id).filter(User.email == to).scalar()
            mail_log.record_direct(
                log_db,
                recipient_email=to,
                recipient_user_id=ruid,
                category=category,
                template_slug=category,
                via=via,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                status=status,
                smtp_code=smtp_code,
                error_class=error_class,
                error_message=error_message,
            )
            log_db.commit()
        except Exception:
            logger.exception("mail_log direct write failed for %s", to)
        finally:
            log_db.close()

    if err is not None:
        raise err


# -------------------------------------------------------------------------
# Test-send (admin diagnostic — synchronous, never queued)
# -------------------------------------------------------------------------


def _smtp_error_hint(exc: Exception) -> str | None:
    """Map a caught SMTP exception to a short, human-readable next step for
    the admin test-send UI. Matches on type-name + SMTP code + lowercased
    message (no aiosmtplib error-class imports) so it stays dependency-light
    and testable. First match wins; never raises (it runs inside the error
    path — a throw would turn a clean diagnostic into a 500)."""
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = None
    text = (str(getattr(exc, "message", "")) + " " + str(exc)).lower()
    name = type(exc).__name__

    # 1. Authentication — check before the relay rule below, since a 535 body
    #    can also contain "access denied".
    if name == "SMTPAuthenticationError" or code == 535 or "5.7.8" in text or "authentication" in text:
        return (
            "SMTP authentication failed (bad username or password). Re-enter "
            "the SMTP user and password; some providers need an "
            "app-specific password."
        )
    # 2. Client / relay refused — today's "554 5.7.1 Client host rejected" case.
    if (
        code == 554
        or "5.7.1" in text
        or "client host rejected" in text
        or "relay access denied" in text
        or "access denied" in text
    ):
        return (
            "The server refused this client. Check the SMTP username and "
            "password and that the server permits this sender to relay "
            "(mynetworks / permit_sasl_authenticated). A correct EHLO/HELO "
            "hostname (the HELO field) often resolves strict-MTA rejections."
        )
    # 3. TLS mismatch — require code is None so a coded 5xx relay/auth reject
    #    can't be misclassified as TLS.
    if name == "SMTPNotSupported" or "starttls" in text or (("tls" in text or "ssl" in text) and code is None):
        return (
            "TLS negotiation failed. The TLS mode likely doesn't match the "
            "port: use 'implicit' for 465, 'starttls' for 587, 'none' only "
            "on a trusted localhost relay."
        )
    # 4. Connection / timeout — last, since some TLS errors subclass these.
    if (
        name in ("SMTPConnectError", "SMTPServerDisconnected", "SMTPTimeoutError", "TimeoutError")
        or isinstance(exc, OSError)
        or "connect" in text
        or "timed out" in text
    ):
        return (
            "Could not reach the SMTP server. Check the host, port, and that "
            "no firewall blocks the outbound connection."
        )
    return None


def _smtp_error_payload(exc: Exception) -> dict:
    """Convert any aiosmtplib / OSError exception into the structured
    diagnostic the admin UI renders. Pulls SMTP code if available."""
    out: dict = {
        "ok": False,
        "error_class": type(exc).__name__,
        "error_message": str(exc) or repr(exc),
        "smtp_code": None,
        "hint": _smtp_error_hint(exc),
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
            "hint": None,
        }
    from . import site as site_svc

    ctx = {"now": datetime.now(tz=timezone.utc).replace(microsecond=0)}
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
        _log_test_send(to, subject, text, html, exc=exc)
        return _smtp_error_payload(exc)
    _log_test_send(to, subject, text, html, exc=None)
    return {
        "ok": True,
        "error_class": None,
        "error_message": None,
        "smtp_code": None,
        "hint": None,
    }


def _log_test_send(
    to: str, subject: str, text: str, html: str | None, *, exc: Exception | None
) -> None:
    """Best-effort mail-log entry for the admin SMTP test-send (via=test).
    Never alters the diagnostic returned to the admin UI."""
    from ..models.email_log import EmailStatus, EmailVia
    from . import mail_log

    code = getattr(exc, "code", None)
    log_db = SessionLocal()
    try:
        mail_log.record_direct(
            log_db,
            recipient_email=to,
            recipient_user_id=None,
            category="smtp_test",
            template_slug="smtp_test",
            via=EmailVia.test,
            subject=subject,
            text_body=text,
            html_body=html,
            status=EmailStatus.sent if exc is None else EmailStatus.failed,
            smtp_code=code if isinstance(code, int) else None,
            error_class=type(exc).__name__ if exc else None,
            error_message=str(exc) if exc else None,
        )
        log_db.commit()
    except Exception:
        logger.exception("mail_log test write failed for %s", to)
    finally:
        log_db.close()


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
    await _send_resolved(to=to, subject=subject, text_body=body, category="verify")


async def send_password_reset_email(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    app_url: str | None = None, site_timezone: str | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {"display_name": display_name, "reset_url": f"{base}/reset-password/{token}"}
    body = _render(locale, "reset_password", "txt", ctx, app_url=base, site_timezone=tz)
    subject = _resolve_subject(_resolve_locale(locale), "reset_password", ctx)
    await _send_resolved(
        to=to, subject=subject, text_body=body, category="reset_password"
    )


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
    await _send_resolved(to=to, subject=subject, text_body=body, category="invite")


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
    await _send_resolved(
        to=to, subject=subject, text_body=body, category="lockout_warning"
    )
