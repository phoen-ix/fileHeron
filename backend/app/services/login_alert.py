"""Sign-in-from-new-device alerts.

Wraps the existing `_record_login_device` machinery (Phase 1b stored
the rows; Phase 7 fires the email/notification). The detection itself
already runs inside `services/auth.py` - this module is just the
dispatcher hook.

Triggered after a successful login when `_record_login_device` returns
True (i.e., a new (UA fingerprint, IP geohash) tuple). Patch-version
UA changes are already suppressed by `utils/ua_fingerprint.py` so a
Chrome auto-update doesn't fire an alert.
"""
from __future__ import annotations

import logging
import re

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.notification import NotificationCategory
from ..models.user import User
from ..utils.timeutil import utc_now
from . import notification as notif_svc

logger = logging.getLogger("fileheron.login_alert")




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
                # Real client IP (request.client.host is the true client IP -
                # uvicorn runs --proxy-headers and Traefik overwrites XFF). The
                # /24 geohash still backs new-device dedup in auth.py; only the
                # alert surfaces the raw IP.
                "ip_address": ip or "unknown",
                "ua_summary": _summarize_ua(ua),
                "user_agent": ua,  # raw header; template gates on truthiness
                "at": utc_now(),
                "account_url": account_url,
            },
            link_url=account_url,
            email_to=user.email,
        )
    except Exception:
        logger.exception("login_alert dispatch failed for user_id=%d", user.id)


# Browser major-version extractors, keyed by the name picked below. Safari
# reports its own version as `Version/NN` (`Safari/605` is the engine), and
# Edge's `Edg.../NN` is matched before the Chrome token it also carries.
_UA_VERSION_RES = {
    "Firefox": re.compile(r"Firefox/(\d+)"),
    "Edge": re.compile(r"Edg[A-Za-z]*/(\d+)"),
    "Chrome": re.compile(r"Chrome/(\d+)"),
    "Safari": re.compile(r"Version/(\d+)"),
}


def _summarize_ua(ua: str) -> str:
    """Reduce a User-Agent header to a short, audit-friendly label.
    Includes the browser MAJOR version when detectable; the OS *version* is
    intentionally omitted (browsers freeze/spoof it).
    Examples: "Firefox 128 on Windows", "Chrome on macOS", "unknown browser"."""
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
    # iOS UAs carry "like Mac OS X", so the iPhone/iPad check MUST precede the
    # macOS check or every iOS device is mislabelled macOS.
    if "iphone" in lower or "ipad" in lower or "ios" in lower:
        os_name = "iOS"
    elif "mac os" in lower or "macos" in lower:
        os_name = "macOS"
    elif "windows" in lower:
        os_name = "Windows"
    elif "android" in lower:
        os_name = "Android"
    elif "linux" in lower:
        os_name = "Linux"
    else:
        os_name = "unknown OS"
    pattern = _UA_VERSION_RES.get(browser)
    m = pattern.search(ua) if pattern else None
    label = f"{browser} {m.group(1)}" if m else browser
    return f"{label} on {os_name}"
