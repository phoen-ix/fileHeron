"""Sign-in-from-new-device alerts.

Wraps the existing `_record_login_device` machinery (Phase 1b stored
the rows; Phase 7 fires the email/notification). The detection itself
already runs inside `services/auth.py` — this module is just the
dispatcher hook.

Triggered after a successful login when `_record_login_device` returns
True (i.e., a new (UA fingerprint, IP geohash) tuple). Patch-version
UA changes are already suppressed by `utils/ua_fingerprint.py` so a
Chrome auto-update doesn't fire an alert.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.notification import NotificationCategory
from ..models.user import User
from ..utils.geohash import ip_geohash5
from . import notification as notif_svc

logger = logging.getLogger("fileheron.login_alert")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def fire_new_device_alert(
    db: Session,
    *,
    user: User,
    request: Request | None,
    via: str,
) -> None:
    """Best-effort: dispatch the notification but never fail the login.
    Caller has already inserted the KnownDevice row + audited."""
    try:
        ip = (request.client.host if (request and request.client) else "") or ""
        ua = (request.headers.get("user-agent", "") if request else "") or ""
        from . import site as site_svc
        account_url = f"{site_svc.get_site_url(db)}/account"
        notif_svc.dispatch(
            db,
            user=user,
            category=NotificationCategory.login_alert,
            payload={
                "display_name": user.display_name,
                "via": via,
                "ip_hint": f"~{ip_geohash5(ip)}" if ip else "unknown",
                # We deliberately don't ship the full UA string — that's
                # enough device-fingerprinting that emailing it back
                # adds disclosure risk if the inbox is later breached.
                "ua_summary": _summarize_ua(ua),
                "at": _utcnow(),
                "account_url": account_url,
            },
            link_url=account_url,
            email_to=user.email,
        )
    except Exception:
        logger.exception("login_alert dispatch failed for user_id=%d", user.id)


def _summarize_ua(ua: str) -> str:
    """Reduce a User-Agent header to a short, audit-friendly label.
    Examples: "Chrome on macOS", "Firefox on Windows", "unknown browser"."""
    if not ua:
        return "unknown browser"
    lower = ua.lower()
    if "firefox" in lower:
        browser = "Firefox"
    elif "edg" in lower:
        browser = "Edge"
    elif "chrome" in lower:
        browser = "Chrome"
    elif "safari" in lower:
        browser = "Safari"
    else:
        browser = "Browser"
    if "mac os" in lower or "macos" in lower:
        os_name = "macOS"
    elif "windows" in lower:
        os_name = "Windows"
    elif "android" in lower:
        os_name = "Android"
    elif "iphone" in lower or "ipad" in lower or "ios" in lower:
        os_name = "iOS"
    elif "linux" in lower:
        os_name = "Linux"
    else:
        os_name = "unknown OS"
    return f"{browser} on {os_name}"
