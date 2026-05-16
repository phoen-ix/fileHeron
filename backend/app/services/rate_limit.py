"""Rate limiting + per-account lockout.

Two layers:
1. Per-IP sliding window on /api/auth/login (Redis INCR + EXPIRE). Returns
   429 RATE_LIMITED when exceeded. Window: 15 minutes.
2. Per-account lockout (DB-backed users.failed_login_count). 5 consecutive
   bad-credential failures → users.locked_until = now + 15 min, warning email
   sent (deduped to one per 6h via users.lockout_email_sent_at).

Successful login resets failed_login_count to 0 and clears locked_until.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import User
from ..redis_client import get_redis
from ..utils.crypto import sha256_hex

if TYPE_CHECKING:
    pass


_LOGIN_RATE_WINDOW_S = 15 * 60
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_DURATION = timedelta(minutes=15)
_LOCKOUT_EMAIL_DEDUP = timedelta(hours=6)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# IP rate limiting
# ---------------------------------------------------------------------------


def _ip_key(ip: str) -> str:
    return f"fh:rl:login:ip:{sha256_hex(ip)[:16]}"


def check_login_ip_allowed(ip: str) -> bool:
    """Returns True if this attempt is allowed (under the per-IP rate limit
    for the current 15-min window). Also INCR-s the counter atomically.
    """
    if not ip:
        return True
    try:
        redis = get_redis()
        key = _ip_key(ip)
        # Atomic INCR; set TTL on first hit.
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, _LOGIN_RATE_WINDOW_S)
        return count <= settings.RATE_LIMIT_LOGIN
    except Exception:
        # Fail-open if Redis is unreachable. Account-level lockout still
        # protects individual users.
        return True


def reset_ip_window(ip: str) -> None:
    """Clear the IP's counter (e.g. on successful login)."""
    if not ip:
        return
    try:
        get_redis().delete(_ip_key(ip))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Generic per-IP sliding window for non-login auth-adjacent endpoints
# (register-from-invite, forgot-password, verify-email).
# ---------------------------------------------------------------------------
#
# Same shape as the login limit but per-bucket key + per-bucket cap, so a
# slow brute-forcer on register doesn't burn the login budget. The fail-open
# behavior on Redis outage matches the login path: account-level invariants
# (single-use invite token, single-use reset token) still hold.


def _bucket_key(bucket: str, ip: str) -> str:
    return f"fh:rl:{bucket}:ip:{sha256_hex(ip)[:16]}"


def check_ip_allowed(bucket: str, ip: str, limit: int, window_sec: int = _LOGIN_RATE_WINDOW_S) -> bool:
    """Return True if `ip` may proceed for this `bucket` under the given limit
    and window. Also INCRs the counter atomically."""
    if not ip or limit <= 0:
        return True
    try:
        redis = get_redis()
        key = _bucket_key(bucket, ip)
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, window_sec)
        return count <= limit
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Per-account lockout
# ---------------------------------------------------------------------------


def is_account_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    return user.locked_until > _utcnow()


def record_failure(db: Session, *, user: User) -> tuple[bool, bool]:
    """Increment failure counter, lock if threshold reached.

    Returns (just_locked, should_send_lockout_email):
    - just_locked: True iff this call transitioned the account to LOCKED.
    - should_send_lockout_email: True iff we crossed the 6h dedup window
      AND this call locked (or was already locked). Caller is responsible
      for sending the email and setting `user.lockout_email_sent_at`.

    Takes a row-level write lock via SELECT … FOR UPDATE so 6 concurrent
    failures serialize through MariaDB and can't all read the same
    pre-increment value (the bypass the audit flagged). SQLite ignores
    FOR UPDATE but is single-threaded in tests, so the same code path
    works in both.
    """
    now = _utcnow()
    # Re-read user with a row-level write lock so concurrent record_failure
    # calls on the same row serialize.
    db.refresh(user, with_for_update=True)

    user.failed_login_count = (user.failed_login_count or 0) + 1
    just_locked = False
    if user.failed_login_count >= _LOCKOUT_THRESHOLD and not is_account_locked(user):
        user.locked_until = now + _LOCKOUT_DURATION
        just_locked = True

    should_email = False
    if just_locked:
        last = user.lockout_email_sent_at
        if last is None or (now - last) > _LOCKOUT_EMAIL_DEDUP:
            should_email = True
    db.flush()
    return just_locked, should_email


def record_success(db: Session, *, user: User) -> None:
    """Reset counter + clear lockout on a successful login."""
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    db.flush()


def mark_lockout_email_sent(db: Session, *, user: User) -> None:
    user.lockout_email_sent_at = _utcnow()
    db.flush()
