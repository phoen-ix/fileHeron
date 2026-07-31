"""Email rendering + sending. Templates live under app/templates/email/{locale}.

Phase 1a: text-only stubs for verify, reset_password, invite.
Phase 6a: HTML variants, locale-aware date formatting, the
`render_email` / `enqueue_email_send` pair the notification dispatcher
calls into, plus a shared `subjects.json` per locale so subject lines
live in i18n alongside the bodies.

Direct senders (verify / reset / invite / lockout) stay synchronous -
the auth flow doesn't depend on a worker being up - but the new
notification flows enqueue via the ARQ `send_email_job` task.
"""
from __future__ import annotations

import html as _htmllib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import nh3
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models.user import Locale
from ..utils.emailing import SmtpConfig, send_email
from . import richtext

DEFAULT_TIMEZONE = "UTC"

logger = logging.getLogger("fileheron.email")

_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates" / "email"

# Templates are named `<slug>.<kind>.j2` (e.g. `share_created.html.j2`),
# so the final extension is always `.j2`. `select_autoescape(["html"])`
# keys on the *trailing* extension and would therefore NEVER autoescape -
# leaving user-controlled fields (subject, message, display_name, filename)
# injected raw into HTML mail. Match on the compound `.html.j2` extension
# (and explicitly leave `.txt.j2` un-escaped - plain text needs raw output).
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_ROOT)),
    autoescape=select_autoescape(
        enabled_extensions=("html.j2",),
        disabled_extensions=("txt.j2",),
        default_for_string=False,
    ),
    keep_trailing_newline=True,
)

# Locale-keyed subject lines. Loaded once at import time for every locale the
# app supports (Locale enum), so adding a locale needs no code change here.
_SUBJECTS: dict[str, dict[str, str]] = {}
for _loc in Locale:
    _path = _TEMPLATE_ROOT / _loc.value / "subjects.json"
    if _path.is_file():
        _SUBJECTS[_loc.value] = json.loads(_path.read_text(encoding="utf-8"))

_LOCALE_CODES = {loc.value for loc in Locale}


def _resolve_locale(locale: Locale | str) -> str:
    code = locale.value if isinstance(locale, Locale) else locale
    return code if code in _LOCALE_CODES else "en"


@pass_context
def _format_dt_locale(jctx, value, locale_code: str = "en") -> str:
    """Jinja filter: format a datetime in the recipient's locale, in the
    admin-set site timezone. Reads ``site_timezone`` from the rendering context;
    delegates the actual formatting to ``email_placeholders.format_dt_locale``
    (single source of truth, shared with the override-render path)."""
    from . import email_placeholders as ep

    return ep.format_dt_locale(value, locale_code, jctx.get("site_timezone"))


_env.filters["dt_locale"] = _format_dt_locale
_ALLOWED_TAGS = {
    "p", "strong", "b", "em", "i", "a", "ul", "ol", "li",
    "blockquote", "code", "pre", "h1", "h2", "h3", "h4", "br", "hr",
}
_ALLOWED_ATTRS = {"a": {"href"}}
_ALLOWED_SCHEMES = {"http", "https", "mailto"}

# Wrap a sanitized HTML fragment in the shared branded layout. The fragment is
# already sanitized → marked safe; the subject is escaped for the <title>.
_LAYOUT_WRAP = _env.from_string(
    "{% extends 'layout.html.j2' %}"
    "{% block subject %}{{ email_subject|e }}{% endblock %}"
    "{% block content %}{{ fragment|safe }}{% endblock %}"
)


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
    provided - safe default for tests + auth paths that haven't been
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


def _sub(text: str, mapping: dict[str, str]) -> str:
    """Single-pass replace of friendly ``[TOKEN]`` occurrences from ``mapping``;
    unknown tokens are left untouched (no cascade - a substituted value can't be
    re-scanned)."""
    from . import email_placeholders as ep

    return ep.TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def _normalize_text(text: str) -> str:
    """Tidy the plain-text part: collapse 3+ blank lines, single trailing NL."""
    return re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"


def _sanitize_html(raw_html: str) -> str:
    return nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_SCHEMES,
    )


# --- HTML override rendering (ProseMirror editor, v1.50) ---------------------
# The shared sanitiser (services/richtext) keeps only the four alignment classes;
# email clients ignore classes/<style>, so we inline just those four as
# `style="text-align:…"`. Everything else in the layout is already inline-styled.
_ALIGN_INLINE = {
    "text-left": "text-align:left",
    "text-center": "text-align:center",
    "text-right": "text-align:right",
    "text-justify": "text-align:justify",
}


def _inline_alignment(html: str) -> str:
    """Inject inline `text-align` for the four alignment utility classes the
    sanitiser preserves (so alignment survives in Outlook/Gmail)."""
    return re.sub(
        r'class="(text-(?:left|center|right|justify))"',
        lambda m: f'class="{m.group(1)}" style="{_ALIGN_INLINE[m.group(1)]}"',
        html,
    )


def _html_to_text(html: str) -> str:
    """Best-effort plain-text alternative from an HTML body: links become
    ``label (href)``, block tags become line breaks, remaining tags are stripped
    and entities decoded. Token placeholders in hrefs survive for substitution."""
    if not html:
        return ""
    t = re.sub(
        r'(?is)<a\b[^>]*\bhref="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})",
        html,
    )
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)<li\b[^>]*>", "- ", t)
    t = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|blockquote|ul|ol|pre)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return _htmllib.unescape(t)


def _wrap_layout(
    fragment: str, subject: str, locale_code: str, app_name: str | None,
    site_timezone: str | None, ctx: dict,
) -> str:
    return _LAYOUT_WRAP.render(
        fragment=fragment,
        email_subject=subject,
        app_name=app_name if app_name else settings.APP_NAME,
        app_url=settings.APP_URL,
        locale=locale_code,
        site_timezone=site_timezone or DEFAULT_TIMEZONE,
        now=ctx.get("now"),
        # Footer links (present only when render_email injected them).
        manage_subscriptions_url=ctx.get("manage_subscriptions_url"),
        unsubscribe_url=ctx.get("unsubscribe_url"),
        brand_logo_url=ctx.get("brand_logo_url"),
    )


# --- Subscription footer (unsubscribe + manage links) ------------------------


def _is_unsubscribable(category: str | None) -> bool:
    """True only for a real, non-locked notification category - the kind a user
    may opt out of. Auth/transactional slugs (verify, invite, email_change_*,
    smtp_test) aren't in the enum, and security categories are locked; both
    return False, so those emails get the Manage link but no per-type
    Unsubscribe."""
    if not category:
        return False
    from ..models.notification import NotificationCategory
    from .notification_prefs import LOCKED_CATEGORIES

    try:
        cat = NotificationCategory(category)
    except ValueError:
        return False
    return cat not in LOCKED_CATEGORIES


def _resolve_recipient_user_id(
    recipient_user_id: int | None, recipient_email: str | None, db: Session | None
) -> int | None:
    if recipient_user_id is not None:
        return recipient_user_id
    if not recipient_email:
        return None
    from ..models.user import User

    own = db is None
    sess = db or SessionLocal()
    try:
        return (
            sess.query(User.id)
            .filter(User.email == recipient_email.lower().strip())
            .scalar()
        )
    except Exception:
        logger.exception("recipient lookup failed for footer")
        return None
    finally:
        if own:
            sess.close()


def _subscription_urls(
    user_id: int, category: str | None, app_url: str | None
) -> tuple[str, str | None]:
    """(manage_url, unsubscribe_url|None) for the email footer. The unsubscribe
    URL is only built for an opt-outable category."""
    from . import unsubscribe_token

    base = (app_url if app_url is not None else settings.APP_URL).rstrip("/")
    token = unsubscribe_token.issue(user_id)
    manage = f"{base}/manage-notifications/{token}"
    unsub = f"{manage}?off={category}" if _is_unsubscribable(category) else None
    return manage, unsub


def list_unsubscribe_header(
    user_id: int, category: str | None, app_url: str | None
) -> str | None:
    """RFC 2369/8058 List-Unsubscribe value for an opt-outable category, else
    None. Points at the one-click API endpoint plus a mailto fallback."""
    if not _is_unsubscribable(category):
        return None
    from . import unsubscribe_token

    base = (app_url if app_url is not None else settings.APP_URL).rstrip("/")
    token = unsubscribe_token.issue(user_id)
    one_click = f"{base}/api/notification-subscriptions/{token}/one-click?category={category}"
    parts = [f"<{one_click}>"]
    if settings.SMTP_FROM_EMAIL:
        parts.append(f"<mailto:{settings.SMTP_FROM_EMAIL}?subject=unsubscribe%20{category}>")
    return ", ".join(parts)


def _brand_logo_url(db: Session | None, app_url: str | None) -> str | None:
    """Absolute URL of the admin logo for the email header, or None when no
    logo is set or the email surface is off. Best-effort - never raises."""
    if db is None:
        return None
    try:
        from . import settings as settings_svc

        if not settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_EMAIL, default=False):
            return None
        if not settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR):
            return None
    except Exception:
        return None
    base = (app_url if app_url is not None else settings.APP_URL).rstrip("/")
    return f"{base}/api/branding/logo"


def _append_text_footer(
    text: str, manage_url: str, unsub_url: str | None, locale_code: str
) -> str:
    lines: list[str] = []
    if locale_code == "de":
        lines.append(f"Benachrichtigungen verwalten: {manage_url}")
        if unsub_url:
            lines.append(f"Diese Art E-Mail abbestellen: {unsub_url}")
    else:
        lines.append(f"Manage subscriptions: {manage_url}")
        if unsub_url:
            lines.append(f"Unsubscribe from these emails: {unsub_url}")
    return text.rstrip("\n") + "\n\n-- \n" + "\n".join(lines) + "\n"


def render_override(
    override, slug: str, locale_code: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
    app_name: str | None = None,
) -> tuple[str, str, str]:
    """Render an admin override (subject, text, html) from its HTML body.

    Security ordering for the HTML part: URL tokens substituted into hrefs (real
    scheme, canonical auth path) BEFORE sanitize, then nh3 sanitize (shared
    allowlist), then text tokens substituted (HTML-escaped) so no user-controlled
    value can introduce markup, then alignment classes inlined for mail clients.
    The plain-text part is derived from the HTML."""
    from . import email_placeholders as ep

    eff_app_url = app_url if app_url is not None else settings.APP_URL
    eff_app_name = app_name if app_name else settings.APP_NAME
    eff_tz = site_timezone or DEFAULT_TIMEZONE
    text_values, html_values = ep.build_substitutions(
        slug, ctx, locale_code=locale_code, app_url=eff_app_url,
        app_name=eff_app_name, site_timezone=eff_tz,
    )
    url_toks = ep.url_tokens(slug)

    raw_subject = override.subject if override.subject else _resolve_subject(
        locale_code, slug, ctx, app_name
    )
    subject = _sub(raw_subject, text_values)

    body_html = override.body_html or ""

    # Plain-text alternative, derived from the HTML (links keep their href so
    # token URLs survive), then text tokens substituted.
    text = _normalize_text(_sub(_html_to_text(body_html), text_values))

    frag = _sub(body_html, {t: html_values[t] for t in url_toks if t in html_values})
    frag = richtext.sanitize_html(frag)
    frag = _sub(frag, {t: v for t, v in html_values.items() if t not in url_toks})
    frag = _inline_alignment(frag)
    html_out = _wrap_layout(frag, subject, locale_code, eff_app_name, eff_tz, ctx)
    return subject, text, html_out


def _load_override(slug: str, locale_code: str, db: Session | None = None):
    """Look up an admin override for (slug, locale), falling back to ``en`` like
    the filesystem path. Opens a short-lived session when ``db`` is None. Returns
    None on any error so a lookup failure can never break a send."""
    from . import email_placeholders as ep

    if not ep.is_editable(slug):
        return None
    from ..models.email_template_override import EmailTemplateOverride

    own = db is None
    sess = db or SessionLocal()
    try:
        row = (
            sess.query(EmailTemplateOverride)
            .filter_by(slug=slug, locale=locale_code)
            .one_or_none()
        )
        if row is None and locale_code != "en":
            row = (
                sess.query(EmailTemplateOverride)
                .filter_by(slug=slug, locale="en")
                .one_or_none()
            )
        return row
    except Exception:
        logger.exception("email override lookup failed for %s/%s", slug, locale_code)
        return None
    finally:
        if own:
            sess.close()


def render_email(
    locale: Locale | str, slug: str, ctx: dict,
    *, app_url: str | None = None, site_timezone: str | None = None,
    app_name: str | None = None, db: Session | None = None,
    recipient_user_id: int | None = None, recipient_email: str | None = None,
    category: str | None = None,
) -> tuple[str, str, str | None]:
    """Render (subject, text, html). HTML may be None if no override exists and
    no .html.j2 ships. Consults the admin override table first (per (slug,
    locale)); falls back to the built-in filesystem template when absent.

    ``app_url`` should be the kv-resolved value from
    ``services.site.get_site_url``; ``site_timezone`` from
    ``services.site.get_site_timezone``; ``app_name`` from
    ``services.site.get_app_name``. All default safely when omitted.

    When the recipient is a known user (``recipient_user_id``, or resolvable from
    ``recipient_email``), a Manage-subscriptions footer link is injected into both
    the HTML (via the layout) and the text body; an Unsubscribe link is added too
    when ``category`` is an opt-outable notification category."""
    code = _resolve_locale(locale)

    manage_url: str | None = None
    unsub_url: str | None = None
    uid = _resolve_recipient_user_id(recipient_user_id, recipient_email, db)
    if uid is not None:
        manage_url, unsub_url = _subscription_urls(uid, category, app_url)
        ctx = {**ctx, "manage_subscriptions_url": manage_url, "unsubscribe_url": unsub_url}

    logo_url = _brand_logo_url(db, app_url)
    if logo_url:
        ctx = {**ctx, "brand_logo_url": logo_url}

    override = _load_override(slug, code, db)
    if override is not None:
        subject, text, html = render_override(
            override, slug, code, ctx,
            app_url=app_url, site_timezone=site_timezone, app_name=app_name,
        )
    else:
        subject = _resolve_subject(code, slug, ctx, app_name)
        text = _render(code, slug, "txt", ctx, app_url=app_url, site_timezone=site_timezone, app_name=app_name)
        try:
            html = _render(code, slug, "html", ctx, app_url=app_url, site_timezone=site_timezone, app_name=app_name)
        except Exception:
            html = None

    if manage_url:
        text = _append_text_footer(text, manage_url, unsub_url, code)
    return subject, text, html


# -------------------------------------------------------------------------
# SMTP config resolution (post-Phase 10 - admin-editable in app_settings,
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
    except Exception as exc:  # noqa: BLE001 - re-raised below after logging
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
# Test-send (admin diagnostic - synchronous, never queued)
# -------------------------------------------------------------------------


def _smtp_error_hint(exc: Exception) -> str | None:
    """Map a caught SMTP exception to a short, human-readable next step for
    the admin test-send UI. Matches on type-name + SMTP code + lowercased
    message (no aiosmtplib error-class imports) so it stays dependency-light
    and testable. First match wins; never raises (it runs inside the error
    path - a throw would turn a clean diagnostic into a 500)."""
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = None
    text = (str(getattr(exc, "message", "")) + " " + str(exc)).lower()
    name = type(exc).__name__

    # 1. Authentication - check before the relay rule below, since a 535 body
    #    can also contain "access denied".
    if name == "SMTPAuthenticationError" or code == 535 or "5.7.8" in text or "authentication" in text:
        return (
            "SMTP authentication failed (bad username or password). Re-enter "
            "the SMTP user and password; some providers need an "
            "app-specific password."
        )
    # 2. Client / relay refused - today's "554 5.7.1 Client host rejected" case.
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
    # 3. TLS mismatch - require code is None so a coded 5xx relay/auth reject
    #    can't be misclassified as TLS.
    if name == "SMTPNotSupported" or "starttls" in text or (("tls" in text or "ssl" in text) and code is None):
        return (
            "TLS negotiation failed. The TLS mode likely doesn't match the "
            "port: use 'implicit' for 465, 'starttls' for 587, 'none' only "
            "on a trusted localhost relay."
        )
    # 4. Connection / timeout - last, since some TLS errors subclass these.
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
    except Exception as exc:  # noqa: BLE001 - surface every error to the admin
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
# in place - they're called from auth flows that should not depend on
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


async def send_password_reset_email(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {"display_name": display_name, "reset_url": f"{base}/reset-password/{token}"}
    subject, body, html = render_email(
        locale, "reset_password", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="reset_password",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="reset_password"
    )


async def send_verification_email(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Confirm-your-address link.

    The `verify` template, its subject entry, its placeholder spec and its
    mail-log auth-link masking have all existed since Phase 1a - only the
    sender was missing, so POST /api/auth/resend-verification minted a token,
    committed it, stashed it on `request.state` and returned `{"ok": true}`
    without sending anything. A user who clicked "resend" got a success
    response and no email, forever (audit 2026-07-30)."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {"display_name": display_name, "verify_url": f"{base}/verify-email/{token}"}
    subject, body, html = render_email(
        locale, "verify", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="verify",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="verify"
    )


async def send_invite_email(
    *, to: str, locale: Locale | str, display_name_hint: str, inviter_display_name: str,
    token: str, app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name_hint": display_name_hint,
        "inviter_display_name": inviter_display_name,
        "register_url": f"{base}/register/{token}",
    }
    subject, body, html = render_email(
        locale, "invite", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="invite",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="invite"
    )


async def send_lockout_warning_email(
    *,
    to: str,
    locale: Locale | str,
    display_name: str,
    locked_until_iso: str,
    ip_hint: str | None,
    app_url: str | None = None,
    site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "locked_until": locked_until_iso,
        "ip_hint": ip_hint or "unknown",
        "reset_url": f"{base}/forgot-password",
    }
    subject, body, html = render_email(
        locale, "lockout_warning", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="lockout_warning",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="lockout_warning"
    )


async def send_password_changed_email(
    *,
    to: str,
    locale: Locale | str,
    display_name: str,
    ip_hint: str | None,
    app_url: str | None = None,
    site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Token-free security notice that the account password was just changed
    (audit L34). Deliberately rendered WITHOUT a recipient/category footer:
    security alerts are not opt-outable, and skipping the footer avoids
    emitting a manage-subscriptions token in a mail that doesn't need one."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "ip_hint": ip_hint or "unknown",
        "reset_url": f"{base}/forgot-password",
    }
    subject, body, html = render_email(
        locale, "password_changed", ctx, app_url=base, site_timezone=tz, db=db,
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="password_changed"
    )


# -------------------------------------------------------------------------
# Email-change senders (v1.13.0). The confirm/verify-old/alert mails carry
# one-time tokens (masked at rest by mail_log via category); the completion
# notice is token-free.
# -------------------------------------------------------------------------


async def send_email_change_confirm(
    *, to: str, locale: Locale | str, display_name: str, token: str,
    new_email: str, by_admin: bool = False,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Confirm link to the NEW address (verify_new + verify_both modes)."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "new_email": new_email,
        "by_admin": by_admin,
        "confirm_url": f"{base}/confirm-email-change/{token}",
    }
    subject, body, html = render_email(
        locale, "email_change_confirm", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="email_change_confirm",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="email_change_confirm"
    )


async def send_email_change_verify_old(
    *, to: str, locale: Locale | str, display_name: str,
    confirm_token: str, cancel_token: str, new_email: str, by_admin: bool = False,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Confirm + cancel links to the OLD address (verify_both mode only)."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "new_email": new_email,
        "by_admin": by_admin,
        "confirm_url": f"{base}/confirm-email-change/{confirm_token}",
        "cancel_url": f"{base}/cancel-email-change/{cancel_token}",
    }
    subject, body, html = render_email(
        locale, "email_change_verify_old", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="email_change_verify_old",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="email_change_verify_old"
    )


async def send_email_change_alert(
    *, to: str, locale: Locale | str, display_name: str, new_email: str,
    cancel_token: str | None = None, by_admin: bool = False, applied: bool = False,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Security notice to the OLD address. When ``applied`` (immediate mode)
    the change is already live, so no cancel link; otherwise (verify_new) a
    cancel link lets the old mailbox kill the pending change."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "new_email": new_email,
        "by_admin": by_admin,
        "applied": applied,
        "cancel_url": f"{base}/cancel-email-change/{cancel_token}" if cancel_token else None,
        "reset_url": f"{base}/forgot-password",
    }
    subject, body, html = render_email(
        locale, "email_change_alert", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="email_change_alert",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="email_change_alert"
    )


async def send_email_change_completed(
    *, to: str, locale: Locale | str, display_name: str, new_email: str,
    oidc_reset: bool = False,
    app_url: str | None = None, site_timezone: str | None = None,
    db: Session | None = None,
) -> None:
    """Token-free courtesy notice to the NEW (now current) address."""
    base = _app_url(app_url)
    tz = _site_tz(site_timezone)
    ctx = {
        "display_name": display_name,
        "new_email": new_email,
        "oidc_reset": oidc_reset,
        "login_url": f"{base}/login",
    }
    subject, body, html = render_email(
        locale, "email_change_completed", ctx, app_url=base, site_timezone=tz, db=db,
        recipient_email=to, category="email_change_completed",
    )
    await _send_resolved(
        to=to, subject=subject, text_body=body, html_body=html, category="email_change_completed"
    )
