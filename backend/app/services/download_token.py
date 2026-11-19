"""Short-lived signed download URL.

Browser-driven `<a href>` downloads can't carry the bearer token (it
lives in memory, not a cookie), so the SPA gets a one-shot signed
URL it can pass to ``window.location.href``. The signature ties the
URL to a specific (file_id, user_id) pair and a short expiry; the
download endpoint accepts it as an alternate auth path.

Token format (compact, URL-safe):

    <user_id>.<iat_unix>.<exp_unix>.<sig_base64url>

where sig = HMAC-SHA256(domain + b"<file_id>|<user_id>|<iat_unix>|<exp_unix>",
JWT_SECRET).

The issue time is there because this URL is a bearer credential standing in for
the session that minted it, so it must honour `users.sessions_invalidated_at`
like the SSE and unsubscribe tokens (`jwt_session.was_issued_before_revocation`).
Without it, URLs a hijacked session minted kept serving bytes for the whole TTL
after "sign out everywhere" or a password reset (audit 2026-11-19). The domain
moved to v2 with the format, so a v1 URL (at most one TTL old) is refused and
the SPA mints a new one.

The lifetime is the caller's choice, and every caller reads it from the
admin-tunable `downloads.signed_url_ttl_sec` (default 900 s) - long enough
for a browser's native Resume to revalidate the same URL, short enough that
a leaked URL doesn't keep working. (This header said "~60 seconds" and the
module carried a 60 s default nothing used, for as long as the setting has
existed.) The token authenticates the caller as the embedded user_id; once
the request hits the download endpoint, the same authorization checks
(`is_authorized_to_download`, file state) run as for a bearer request.

**Audit-trail attribution caveat**: a signed URL minted by Alice
can be forwarded to Bob (within its window). Bob's IP
and user-agent will appear in the `download_log` row, but
`accessed_by_user_id` will say Alice - because the token is the
proof of identity, not the request. This is by design (the SPA
flow needs a bearer-less navigation path) and the short TTL keeps
the abuse window small. If stricter attribution is needed in the
future, bind the token to the issuing IP / UA fingerprint at
issuance.
"""
from __future__ import annotations

from ..middleware.errors import AppError
from ..utils import signed_token
from ..utils.crypto import constant_time_equals

_now = signed_token.now_epoch


# Domain-separation prefix so a download-URL HMAC can never collide with another
# HMAC that also keys on JWT_SECRET (SSE token, unsubscribe token, unlock cookie)
# even if a payload structure ever lined up (audit Info-2). Bumping the version
# suffix invalidates outstanding tokens, which is harmless at a TTL of minutes.
_DOMAIN = b"fh:download-url:v2\x00"


def _sign(payload: bytes) -> str:
    return signed_token.sign(payload, domain=_DOMAIN)


def issue(file_id: str, user_id: int, ttl_sec: int) -> str:
    """Mint a signed download token for (file_id, user_id) with ttl. The TTL
    is required: the only source of it is the settings registry, and a
    default here would be a second definition that drifts."""
    iat = _now()
    exp = iat + ttl_sec
    sig = _sign(f"{file_id}|{user_id}|{iat}|{exp}".encode())
    return f"{user_id}.{iat}.{exp}.{sig}"


def verify_full(file_id: str, token: str) -> tuple[int, int]:
    """Parse + verify a token. Returns (user_id, issued_at_epoch); the caller
    checks the issue time against the user's revocation mark.
    Raises AppError(401) on any failure (malformed, expired, bad
    signature, or signed for a different file)."""
    try:
        user_str, iat_str, exp_str, sig = token.split(".", 3)
        user_id = int(user_str)
        iat = int(iat_str)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        raise AppError(
            401, "INVALID_DOWNLOAD_TOKEN", "Bad download token."
        ) from None

    if exp < _now():
        raise AppError(401, "DOWNLOAD_TOKEN_EXPIRED", "Download token expired.")

    expected = _sign(f"{file_id}|{user_id}|{iat}|{exp}".encode())
    # Constant-time compare to defeat timing oracles.
    if not constant_time_equals(expected, sig):
        raise AppError(401, "INVALID_DOWNLOAD_TOKEN", "Bad download token.")

    return user_id, iat


def verify(file_id: str, token: str) -> int:
    """`verify_full` without the issue time: just the user_id."""
    return verify_full(file_id, token)[0]
