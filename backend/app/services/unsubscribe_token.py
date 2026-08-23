"""Long-lived signed token for the anonymous "manage subscriptions" page.

Email footers carry a Manage-subscriptions link (and, for opt-outable
categories, an Unsubscribe link). Both must work when the recipient is NOT
logged in, so the link embeds a signed token that identifies the user. The
manage page is idempotent (the recipient just tunes their notification
preferences), so - unlike password-reset - the token is **stateless** (no DB
row, no single-use gate) and re-usable: the same link keeps working across the
many emails a user receives.

The pattern mirrors ``services/sse_token.py`` / ``services/download_token.py``:

    <user_id>.<iat_unix>.<exp_unix>.<sig_base64url>

where sig = HMAC-SHA256(b"notif-mgmt|<user_id>|<iat_unix>|<exp_unix>",
JWT_SECRET). All four fields are signed.

The ``"notif-mgmt|"`` domain prefix prevents cross-context confusion with the
other JWT_SECRET-signed tokens (SSE, download).

**Why there is an ``iat``.** This token is a second bearer credential for a
user: it reads their display name and their whole notification-preference
matrix, and it mutates that matrix. It carried no issue time, so it could not
be compared against ``users.sessions_invalidated_at`` - which meant a password
change, a password reset, "sign out all other sessions", an admin revoke-all
and an API-token revocation all left it working, for up to 180 days. Only
rotating ``JWT_SECRET`` revoked it. CLAUDE.md states the rule it broke: every
signed token standing in for a session goes through
``jwt_session.was_issued_before_revocation``.

**Legacy three-part tokens are still accepted**, deliberately, and are reported
as having no issue time so the caller skips the revocation check for them.
``sse_token`` refuses its old format instead, and can: its TTL is five minutes,
so that population drains almost immediately. These live in mail already
delivered - refusing them would break every Manage-subscriptions and RFC 8058
one-click link in flight, which is the opposite of protecting the recipient.
They age out on their own within one TTL.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from ..config import settings
from ..middleware.errors import AppError
from ..utils.crypto import constant_time_equals

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
    iat = _now()
    exp = iat + ttl_sec
    sig = _sign(f"notif-mgmt|{user_id}|{iat}|{exp}".encode())
    return f"{user_id}.{iat}.{exp}.{sig}"


def verify_full(token: str) -> tuple[int, int | None]:
    """Parse + verify a token. Returns (user_id, issued_at_epoch).

    ``issued_at_epoch`` is ``None`` for a legacy three-part token, which the
    caller must read as "cannot be revocation-checked" - NOT as "issued at the
    epoch", which would make every legacy token look older than any revocation
    and lock the whole population out. See the module docstring for why they
    are still accepted.

    Raises AppError(401) on any failure (malformed, expired, bad signature).
    """
    parts = token.split(".") if isinstance(token, str) else []
    try:
        if len(parts) == 4:
            user_id, iat_i, exp = int(parts[0]), int(parts[1]), int(parts[2])
            sig = parts[3]
            signed = f"notif-mgmt|{user_id}|{iat_i}|{exp}".encode()
            iat: int | None = iat_i
        elif len(parts) == 3:
            user_id, exp = int(parts[0]), int(parts[1])
            sig = parts[2]
            signed = f"notif-mgmt|{user_id}|{exp}".encode()
            iat = None
        else:
            raise ValueError("wrong number of parts")
    except (ValueError, AttributeError):
        raise AppError(401, "INVALID_MANAGE_TOKEN", "Bad manage link.") from None

    if exp < _now():
        raise AppError(401, "MANAGE_TOKEN_EXPIRED", "This manage link has expired.")

    # Constant-time compare to defeat timing oracles. Verified against the
    # payload shape the token's OWN format implies, so a three-part token can
    # never be replayed as a four-part one (or the reverse) - the signed string
    # differs, so the signature cannot match across formats.
    if not constant_time_equals(_sign(signed), sig):
        raise AppError(401, "INVALID_MANAGE_TOKEN", "Bad manage link.")

    return user_id, iat


def verify(token: str) -> int:
    """Back-compat wrapper: just the user id, no revocation check possible."""
    return verify_full(token)[0]
