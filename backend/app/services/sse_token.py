"""Short-lived signed token for SSE auth.

The browser's ``EventSource`` API cannot attach custom HTTP headers,
only cookies - and our refresh cookie is scoped to ``/api/auth/`` so
it never reaches ``/api/notifications/stream``. Without this token
flow, every SSE connect would 401, and the SPA would burn CPU in a
reconnect storm (the Bell component starts the stream the moment the
user is authenticated).

The pattern is the SSE counterpart to ``services/download_token.py``:
the SPA fetches a short-lived signed token from a bearer-authed
endpoint, then opens the EventSource with ``?token=<token>`` in the
URL. The stream endpoint accepts either the token (browser path) or a
bearer header (curl/CI path).

Token format (compact, URL-safe, mirrors ``download_token``):

    <user_id>.<exp_unix>.<sig_base64url>

where sig = HMAC-SHA256(b"sse|<user_id>|<exp_unix>", JWT_SECRET).

The ``"sse|"`` domain prefix prevents cross-context confusion if a
future signed-token feature ever uses the same secret with a
different payload shape.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from ..config import settings
from ..middleware.errors import AppError
from ..utils.crypto import constant_time_equals

# 5 minutes. The SPA mints a fresh token on every (re)connect and the
# server closes the stream every 60s by design (see CLAUDE.md). A 2-minute
# TTL used to expire during throttled/background-tab reconnects (browsers
# defer the connect long past the mint), surfacing as a 401 on the stream;
# 5 minutes comfortably outlives that window while staying short-lived.
DEFAULT_TTL_SEC = 300


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _sign(payload: bytes) -> str:
    secret = settings.JWT_SECRET.encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue(user_id: int, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Mint a signed SSE token for `user_id` with `ttl_sec` lifetime."""
    exp = _now() + ttl_sec
    payload = f"sse|{user_id}|{exp}".encode()
    sig = _sign(payload)
    return f"{user_id}.{exp}.{sig}"


def verify(token: str) -> int:
    """Parse + verify a token. Returns the user_id it was issued for.
    Raises AppError(401) on any failure (malformed, expired, or bad
    signature)."""
    try:
        user_str, exp_str, sig = token.split(".", 2)
        user_id = int(user_str)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        raise AppError(401, "INVALID_SSE_TOKEN", "Bad SSE token.") from None

    if exp < _now():
        raise AppError(401, "SSE_TOKEN_EXPIRED", "SSE token expired.")

    expected = _sign(f"sse|{user_id}|{exp}".encode())
    # Constant-time compare to defeat timing oracles.
    if not constant_time_equals(expected, sig):
        raise AppError(401, "INVALID_SSE_TOKEN", "Bad SSE token.")

    return user_id
