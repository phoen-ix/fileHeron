"""Short-lived signed download URL.

Browser-driven `<a href>` downloads can't carry the bearer token (it
lives in memory, not a cookie), so the SPA gets a one-shot signed
URL it can pass to ``window.location.href``. The signature ties the
URL to a specific (file_id, user_id) pair and a short expiry; the
download endpoint accepts it as an alternate auth path.

Token format (compact, URL-safe):

    <user_id>.<exp_unix>.<sig_base64url>

where sig = HMAC-SHA256(b"<file_id>|<user_id>|<exp_unix>", JWT_SECRET).

Lives ~60 seconds - long enough to start a multi-GB download but
short enough that a leaked URL doesn't keep working. The token
authenticates the caller as the embedded user_id; once the request
hits the download endpoint, the same authorization checks
(`is_authorized_to_download`, file state) run as for a bearer
request.

**Audit-trail attribution caveat**: a signed URL minted by Alice
can be forwarded to Bob (within its 60-second window). Bob's IP
and user-agent will appear in the `download_log` row, but
`accessed_by_user_id` will say Alice - because the token is the
proof of identity, not the request. This is by design (the SPA
flow needs a bearer-less navigation path) and the short TTL keeps
the abuse window small. If stricter attribution is needed in the
future, bind the token to the issuing IP / UA fingerprint at
issuance.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from ..config import settings
from ..middleware.errors import AppError

# 60 seconds. Long enough to start the download (the FileResponse
# stream then runs to completion regardless of token expiry - the
# token only gates the initial GET).
DEFAULT_TTL_SEC = 60


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _sign(payload: bytes) -> str:
    secret = settings.JWT_SECRET.encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue(file_id: str, user_id: int, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Mint a signed download token for (file_id, user_id) with ttl."""
    exp = _now() + ttl_sec
    payload = f"{file_id}|{user_id}|{exp}".encode()
    sig = _sign(payload)
    return f"{user_id}.{exp}.{sig}"


def verify(file_id: str, token: str) -> int:
    """Parse + verify a token. Returns the user_id it was issued for.
    Raises AppError(401) on any failure (malformed, expired, bad
    signature, or signed for a different file)."""
    try:
        user_str, exp_str, sig = token.split(".", 2)
        user_id = int(user_str)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        raise AppError(
            401, "INVALID_DOWNLOAD_TOKEN", "Bad download token."
        ) from None

    if exp < _now():
        raise AppError(401, "DOWNLOAD_TOKEN_EXPIRED", "Download token expired.")

    expected = _sign(f"{file_id}|{user_id}|{exp}".encode())
    # Constant-time compare to defeat timing oracles.
    if not hmac.compare_digest(expected, sig):
        raise AppError(401, "INVALID_DOWNLOAD_TOKEN", "Bad download token.")

    return user_id
