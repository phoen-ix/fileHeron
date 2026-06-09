"""Friendly placeholder registry for admin-editable email templates (v1.25.0).

Admins author email bodies/subjects with friendly tokens like ``[SENDER]`` or
``[SHARE_LINK]`` instead of raw Jinja. This module is the single source of truth
mapping each template's tokens to the underlying render-context keys, and turns a
render context into the concrete substitution values.

Security notes:
- ``build_substitutions`` returns two value maps - ``text_values`` (raw, for the
  plain-text part and the subject) and ``html_values`` (HTML-escaped for text
  tokens, attribute-escaped URLs) so user-controlled values can never inject
  markup into the HTML body.
- ``auth_link`` tokens carry one-time tokens; their value keeps the canonical URL
  path (e.g. ``/reset-password/<token>``) so ``mail_log`` masking still redacts
  them at rest. The senders already build these links - we only pass them through.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from babel.dates import format_date, format_time

DEFAULT_TIMEZONE = "UTC"

# A friendly token: bracketed uppercase ASCII. Used to scan a body/subject.
TOKEN_RE = re.compile(r"\[[A-Z_]+\]")


@dataclass(frozen=True)
class Placeholder:
    token: str  # e.g. "[SHARE_LINK]" - what the admin types
    label: str
    description: str
    context_key: str  # key in the render ctx this maps to
    kind: str = "text"  # "text" | "url" | "datetime"
    required: bool = False  # PUT validation rejects a body missing this token
    auth_link: bool = False  # value is a one-time-token link (keep canonical path)


@dataclass(frozen=True)
class TemplateSpec:
    slug: str
    group: str  # UI grouping: "shares" | "account" | "security" | "system"
    placeholders: tuple[Placeholder, ...] = field(default_factory=tuple)


# Available in every template.
_COMMON: tuple[Placeholder, ...] = (
    Placeholder("[APP_NAME]", "App name", "The application name (e.g. file:Heron).", "app_name"),
    Placeholder("[APP_URL]", "App URL", "Base URL of this installation.", "app_url", kind="url"),
)


def _p(*placeholders: Placeholder) -> tuple[Placeholder, ...]:
    return _COMMON + placeholders


# Friendly tokens reused across templates, defined once for consistency.
_RECIPIENT = Placeholder(
    "[RECIPIENT]", "Recipient name", "Display name of the person receiving the email.", "recipient_name"
)
_SENDER = Placeholder("[SENDER]", "Sender name", "Display name of the person who sent the share.", "sender_name")
_SUBJECT = Placeholder("[SUBJECT]", "Share subject", "The share's subject/title.", "subject")
_SHARE_LINK = Placeholder("[SHARE_LINK]", "Share link", "URL to open the share.", "share_url", kind="url")


REGISTRY: dict[str, TemplateSpec] = {
    # ---- Shares -----------------------------------------------------------
    "share_created": TemplateSpec("share_created", "shares", _p(
        _SENDER, _RECIPIENT,
        Placeholder("[FILE_COUNT]", "File count", "Number of files in the share.", "file_count"),
        _SUBJECT,
        Placeholder("[MESSAGE]", "Message", "Optional message from the sender.", "message"),
        Placeholder("[EXPIRES_AT]", "Expiry", "When the share expires.", "expires_at", kind="datetime"),
        _SHARE_LINK,
    )),
    "share_files_added": TemplateSpec("share_files_added", "shares", _p(
        _SENDER, _RECIPIENT,
        Placeholder("[FILE_COUNT]", "Added file count", "Number of newly added files.", "added_count"),
        _SUBJECT, _SHARE_LINK,
    )),
    "share_expiring": TemplateSpec("share_expiring", "shares", _p(
        _RECIPIENT, _SUBJECT,
        Placeholder("[EXPIRES_AT]", "Expiry", "When the share expires.", "expires_at", kind="datetime"),
        _SHARE_LINK,
    )),
    "share_pending_approval": TemplateSpec("share_pending_approval", "shares", _p(
        _SENDER, _RECIPIENT, _SUBJECT, _SHARE_LINK,
    )),
    "share_approved": TemplateSpec("share_approved", "shares", _p(
        _RECIPIENT, _SUBJECT, _SHARE_LINK,
    )),
    "share_rejected": TemplateSpec("share_rejected", "shares", _p(
        _RECIPIENT, _SUBJECT,
        Placeholder("[REASON]", "Rejection reason", "Why the share was rejected.", "reason"),
        _SHARE_LINK,
    )),
    "public_link_downloaded": TemplateSpec("public_link_downloaded", "shares", _p(
        Placeholder("[OWNER]", "Owner name", "Display name of the share owner.", "owner_name"),
        _SUBJECT,
        Placeholder("[FILENAME]", "File name", "Name of the downloaded file.", "filename"),
        Placeholder("[FILE_SIZE]", "File size", "Size of the file in bytes.", "size_bytes"),
        Placeholder("[DOWNLOADED_AT]", "Downloaded at", "When the download happened.", "at", kind="datetime"),
        Placeholder(
            "[DOWNLOADS_REMAINING]", "Downloads remaining", "Remaining downloads on the link.",
            "downloads_remaining",
        ),
        _SHARE_LINK,
    )),
    "file_quarantined": TemplateSpec("file_quarantined", "shares", _p(
        Placeholder("[UPLOADER]", "Uploader name", "Display name of who uploaded the file.", "uploader_name"),
        Placeholder("[FILENAME]", "File name", "Name of the quarantined file.", "filename"),
        Placeholder("[THREAT]", "Threat name", "Antivirus signature / threat name.", "signature"),
    )),
    # ---- Account ----------------------------------------------------------
    "account_created": TemplateSpec("account_created", "account", _p(
        Placeholder("[INVITER]", "Inviter name", "Display name of the inviter.", "inviter_name"),
        Placeholder("[INVITEE]", "New account name", "Display name of the new account.", "invitee_name"),
        Placeholder("[ROLE]", "Role", "Role assigned to the new account.", "invitee_role"),
        Placeholder("[ACCOUNT_LINK]", "Account link", "URL to the user management page.", "account_url", kind="url"),
    )),
    "login_alert": TemplateSpec("login_alert", "account", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the account owner.", "display_name"),
        Placeholder("[LOGIN_AT]", "Sign-in time", "When the sign-in happened.", "at", kind="datetime"),
        Placeholder("[METHOD]", "Sign-in method", "How they signed in (password/oidc/passkey).", "via"),
        Placeholder("[DEVICE]", "Device", "Browser / device summary.", "ua_summary"),
        Placeholder("[IP]", "IP address", "Real client IP of the sign-in.", "ip_address"),
        Placeholder("[USER_AGENT]", "User agent", "Full raw User-Agent header.", "user_agent"),
        Placeholder("[ACCOUNT_LINK]", "Account link", "URL to review account sessions.", "account_url", kind="url"),
    )),
    "oidc_linked": TemplateSpec("oidc_linked", "account", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the account owner.", "user_name"),
        Placeholder("[PROVIDER]", "Provider", "Name of the linked sign-in provider.", "provider_name"),
        Placeholder("[ACCOUNT_LINK]", "Account link", "URL to review account settings.", "account_url", kind="url"),
    )),
    "session_evicted": TemplateSpec("session_evicted", "account", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the account owner.", "display_name"),
        Placeholder("[SESSION_COUNT]", "Evicted sessions", "Number of sessions signed out.", "count"),
        Placeholder("[SESSION_CAP]", "Session cap", "Maximum allowed active sessions.", "cap"),
    )),
    # ---- Security / auth (token-bearing) ----------------------------------
    "verify": TemplateSpec("verify", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder(
            "[VERIFY_LINK]", "Verify link", "One-time link to confirm the email address.",
            "verify_url", kind="url", required=True, auth_link=True,
        ),
    )),
    "reset_password": TemplateSpec("reset_password", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder(
            "[RESET_LINK]", "Reset link", "One-time link to reset the password.",
            "reset_url", kind="url", required=True, auth_link=True,
        ),
    )),
    "invite": TemplateSpec("invite", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Expected name of the invited person.", "display_name_hint"),
        Placeholder("[INVITER]", "Inviter name", "Display name of the inviter.", "inviter_display_name"),
        Placeholder(
            "[INVITE_LINK]", "Invite link", "One-time link to accept the invite.",
            "register_url", kind="url", required=True, auth_link=True,
        ),
    )),
    "lockout_warning": TemplateSpec("lockout_warning", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder("[LOCKED_UNTIL]", "Locked until", "When the lockout expires.", "locked_until"),
        Placeholder("[IP]", "IP hint", "Approximate IP that triggered the lockout.", "ip_hint"),
        Placeholder("[RESET_LINK]", "Password help link", "Link to the forgot-password page.", "reset_url", kind="url"),
    )),
    "email_change_confirm": TemplateSpec("email_change_confirm", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder("[NEW_EMAIL]", "New email", "The new email address being set.", "new_email"),
        Placeholder(
            "[CONFIRM_LINK]", "Confirm link", "One-time link to confirm the new address.",
            "confirm_url", kind="url", required=True, auth_link=True,
        ),
    )),
    "email_change_verify_old": TemplateSpec("email_change_verify_old", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder("[NEW_EMAIL]", "New email", "The new email address being set.", "new_email"),
        Placeholder(
            "[CONFIRM_LINK]", "Confirm link", "One-time link to approve the change.",
            "confirm_url", kind="url", required=True, auth_link=True,
        ),
        Placeholder(
            "[CANCEL_LINK]", "Cancel link", "One-time link to cancel the change.",
            "cancel_url", kind="url", auth_link=True,
        ),
    )),
    "email_change_alert": TemplateSpec("email_change_alert", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder("[NEW_EMAIL]", "New email", "The new email address being set.", "new_email"),
        Placeholder(
            "[CANCEL_LINK]", "Cancel link", "One-time link to cancel the change (may be absent).",
            "cancel_url", kind="url", auth_link=True,
        ),
        Placeholder("[RESET_LINK]", "Password help link", "Link to the forgot-password page.", "reset_url", kind="url"),
    )),
    "email_change_completed": TemplateSpec("email_change_completed", "security", _p(
        Placeholder("[RECIPIENT]", "Recipient name", "Display name of the recipient.", "display_name"),
        Placeholder("[NEW_EMAIL]", "New email", "The new (now current) email address.", "new_email"),
        Placeholder("[LOGIN_LINK]", "Sign-in link", "Link to the sign-in page.", "login_url", kind="url"),
    )),
    # ---- System -----------------------------------------------------------
    "release_available": TemplateSpec("release_available", "system", _p(
        _RECIPIENT,
        Placeholder("[VERSION]", "New version", "The newly available version.", "version"),
        Placeholder("[RUNNING_VERSION]", "Running version", "The currently running version.", "running_version"),
        Placeholder("[RELEASE_LINK]", "Release notes link", "URL to the release notes.", "release_url", kind="url"),
    )),
}


# ---------------------------------------------------------------------------
# Datetime formatting (single source - email.py's Jinja filter delegates here).
# ---------------------------------------------------------------------------


def format_dt_locale(value, locale_code: str = "en", tz_name: str | None = None) -> str:
    """Format a (naive UTC or aware) datetime in the recipient locale + site tz.
    Mirrors the long-standing email date convention: locale date style, 24h time,
    explicit timezone suffix."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            # Non-datetime input (e.g. a friendly [TOKEN] used when seeding the
            # editor's default body) - pass it through unchanged.
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    name = tz_name or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        name = DEFAULT_TIMEZONE
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    locale_str = "de_AT" if locale_code == "de" else "en_US"
    local = dt.astimezone(tz)
    rendered = (
        f"{format_date(local, format='medium', locale=locale_str)}, "
        f"{format_time(local, format='HH:mm:ss', locale=locale_str)}"
    )
    return f"{rendered} ({name})"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_editable(slug: str) -> bool:
    return slug in REGISTRY


def known_tokens(slug: str) -> set[str]:
    spec = REGISTRY.get(slug)
    return {p.token for p in spec.placeholders} if spec else set()


def required_tokens(slug: str) -> set[str]:
    spec = REGISTRY.get(slug)
    return {p.token for p in spec.placeholders if p.required} if spec else set()


def url_tokens(slug: str) -> set[str]:
    """Tokens whose value is a URL (substituted into hrefs before sanitize)."""
    spec = REGISTRY.get(slug)
    return {p.token for p in spec.placeholders if p.kind == "url"} if spec else set()


def tokens_in(text: str | None) -> set[str]:
    return set(TOKEN_RE.findall(text or ""))


def placeholders_for_ui(slug: str) -> list[dict]:
    spec = REGISTRY.get(slug)
    if not spec:
        return []
    return [
        {
            "token": p.token,
            "label": p.label,
            "description": p.description,
            "kind": p.kind,
            "required": p.required,
        }
        for p in spec.placeholders
    ]


def _format_value(p: Placeholder, ctx: dict, *, locale_code: str, tz_name: str,
                  app_url: str, app_name: str) -> str:
    """Resolve one placeholder to its raw (un-escaped) string value."""
    if p.context_key == "app_name":
        return app_name
    if p.context_key == "app_url":
        return app_url
    raw = ctx.get(p.context_key)
    if raw is None:
        return ""
    if p.kind == "datetime":
        return format_dt_locale(raw, locale_code, tz_name)
    return str(raw)


def build_substitutions(
    slug: str, ctx: dict, *, locale_code: str, app_url: str, app_name: str,
    site_timezone: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (text_values, html_values) keyed by token.

    text_values are raw (plain-text body + subject). html_values are
    HTML-escaped for text tokens and attribute-escaped for URL tokens, so a
    user-controlled value can never introduce markup into the HTML body."""
    spec = REGISTRY.get(slug)
    text_values: dict[str, str] = {}
    html_values: dict[str, str] = {}
    if not spec:
        return text_values, html_values
    for p in spec.placeholders:
        raw = _format_value(
            p, ctx, locale_code=locale_code, tz_name=site_timezone,
            app_url=app_url, app_name=app_name,
        )
        text_values[p.token] = raw
        # quote=True escapes quotes too, making it safe inside an href="" attr.
        html_values[p.token] = html.escape(raw, quote=True)
    return text_values, html_values


def sample_ctx(slug: str, *, app_url: str) -> dict:
    """Realistic render context for preview / test-send. Keyed by the underlying
    context keys (so it flows through build_substitutions unchanged). Auth links
    use the canonical path with a dummy token so masking is exercised too."""
    from ..utils.timeutil import utc_now

    soon = utc_now()
    base = {
        "sender_name": "Ada Lovelace",
        "recipient_name": "Grace Hopper",
        "display_name": "Grace Hopper",
        "display_name_hint": "Grace Hopper",
        "user_name": "Grace Hopper",
        "owner_name": "Grace Hopper",
        "inviter_name": "Ada Lovelace",
        "inviter_display_name": "Ada Lovelace",
        "invitee_name": "Grace Hopper",
        "invitee_role": "employee",
        "file_count": 3,
        "added_count": 2,
        "subject": "Q2 designs",
        "message": "Here are the files we discussed.",
        "reason": "Please remove the internal pricing sheet before sharing.",
        "expires_at": soon,
        "at": soon,
        "filename": "designs.zip",
        "size_bytes": 1048576,
        "downloads_remaining": 4,
        "provider_name": "Microsoft Entra ID",
        "via": "password",
        "ua_summary": "Firefox 128 on Windows",
        "ip_hint": "203.0.113.0",
        "ip_address": "203.0.113.42",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/128.0",
        "count": 1,
        "cap": 10,
        "new_email": "grace.hopper@example.com",
        "locked_until": "2026-06-06T15:30:00",
        "version": "1.25.0",
        "running_version": "1.24.0",
        "share_url": f"{app_url}/s/SAMPLE",
        "account_url": f"{app_url}/admin/users/1",
        "login_url": f"{app_url}/login",
        "release_url": f"{app_url}/admin/system",
        # auth links - canonical paths with a dummy token
        "verify_url": f"{app_url}/verify-email/SAMPLETOKEN",
        "reset_url": f"{app_url}/reset-password/SAMPLETOKEN",
        "register_url": f"{app_url}/register/SAMPLETOKEN",
        "confirm_url": f"{app_url}/confirm-email-change/SAMPLETOKEN",
        "cancel_url": f"{app_url}/cancel-email-change/SAMPLETOKEN",
    }
    # lockout / email_change_alert use the token-free forgot-password link.
    if slug in ("lockout_warning", "email_change_alert"):
        base["reset_url"] = f"{app_url}/forgot-password"
    return base
