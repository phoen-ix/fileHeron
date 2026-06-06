"""Long-lived signed token for the anonymous "manage subscriptions" page.

Email footers carry a Manage-subscriptions link (and, for opt-outable
categories, an Unsubscribe link). Both must work when the recipient is NOT
logged in, so the link embeds a signed token that identifies the user. The
manage page is idempotent (the recipient just tunes their notification
preferences), so - unlike password-reset - the token is **stateless** (no DB
row, no single-use gate) and re-usable: the same link keeps working across the
many emails a user receives.

The pattern mirrors ``services/sse_token.py`` / ``services/download_token.py``:

    <user_id>.<exp_unix>.<sig_base64url>

where sig = HMAC-SHA256(b"notif-mgmt|<user_id>|<exp_unix>", JWT_SECRET).

The ``"notif-mgmt|"`` domain prefix prevents cross-context confusion with the
other JWT_SECRET-signed tokens (SSE, download).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from ..config import settings
from ..middleware.errors import AppError

# 180 days. Manage links should outlive the email they ride in by a comfortable
# margin so an old notification's link still works; every fresh email mints a
# new one anyway. The action behind the token is low-risk (toggling the user's
# own notification channels), so a long lifetime is acceptable.
DEFAULT_TTL_SEC = 180 * 24 * 60 * 60


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _sign(payload: bytes) -> str:
    secret = settings.JWT_SECRET.encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue(user_id: int, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Mint a signed manage-subscriptions token for `user_id`."""
    exp = _now() + ttl_sec
    payload = f"notif-mgmt|{user_id}|{exp}".encode()
    sig = _sign(payload)
    return f"{user_id}.{exp}.{sig}"


def verify(token: str) -> int:
    """Parse + verify a token. Returns the user_id it was issued for. Raises
    AppError(401) on any failure (malformed, expired, or bad signature)."""
    try:
        user_str, exp_str, sig = token.split(".", 2)
        user_id = int(user_str)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        raise AppError(401, "INVALID_MANAGE_TOKEN", "Bad manage link.") from None

    if exp < _now():
        raise AppError(401, "MANAGE_TOKEN_EXPIRED", "This manage link has expired.")

    expected = _sign(f"notif-mgmt|{user_id}|{exp}".encode())
    # Constant-time compare to defeat timing oracles.
    if not hmac.compare_digest(expected, sig):
        raise AppError(401, "INVALID_MANAGE_TOKEN", "Bad manage link.")

    return user_id
