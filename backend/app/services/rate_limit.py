"""Rate limiting + per-account lockout.

Two layers:
1. Per-IP fixed window on /api/auth/login (Redis INCR + EXPIRE-on-first-hit).
   Returns 429 RATE_LIMITED when exceeded. Window: 15 minutes. (Fixed, not
   sliding - a burst can straddle two adjacent windows; the per-account
   lockout is the hard stop.)
2. Per-account lockout (DB-backed users.failed_login_count). 5 consecutive
   bad-credential failures → users.locked_until = now + 15 min, warning email
   sent (deduped to one per 6h via users.lockout_email_sent_at).

Successful login resets failed_login_count to 0 and clears locked_until.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import User
from ..redis_client import get_redis
from ..utils.crypto import sha256_hex
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    pass

logger = logging.getLogger("fileheron.rate_limit")

_LOGIN_RATE_WINDOW_S = 15 * 60
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_DURATION = timedelta(minutes=15)
_LOCKOUT_EMAIL_DEDUP = timedelta(hours=6)


# In-process fixed-window fallback used ONLY when Redis is unreachable.
# Without it the per-IP limits fail fully open, leaving credential-stuffing
# from one IP unthrottled during a Redis outage (finding M8). This bounds
# attempts per worker process; it isn't shared across workers (that's what
# Redis is for) but turns "unlimited" into "limit × worker_count".
_local_lock = threading.Lock()
_local_windows: dict[str, tuple[int, float]] = {}  # key -> (count, expiry_monotonic)


def _local_allow(key: str, limit: int, window_sec: int) -> bool:
    if limit <= 0:
        return True
    now = time.monotonic()
    with _local_lock:
        count, expiry = _local_windows.get(key, (0, 0.0))
        if now >= expiry:
            count, expiry = 0, now + window_sec
        count += 1
        _local_windows[key] = (count, expiry)
        # Opportunistic prune so the dict can't grow without bound.
        if len(_local_windows) > 4096:
            for k in [k for k, (_, e) in _local_windows.items() if e <= now]:
                _local_windows.pop(k, None)
    return count <= limit




# ---------------------------------------------------------------------------
# IP rate limiting
# ---------------------------------------------------------------------------


def _ip_key(ip: str) -> str:
    return f"fh:rl:login:ip:{sha256_hex(ip)[:16]}"


def check_login_ip_allowed(
    ip: str, limit: int | None = None, window_sec: int | None = None
) -> bool:
    """Returns True if this attempt is allowed (under the per-IP rate limit
    for the current window). Also INCR-s the counter atomically.

    `limit`/`window_sec` let the caller pass admin-tunable values resolved
    from the settings registry (it has the db); both fall back to the env
    defaults when omitted.
    """
    if not ip:
        return True
    eff_limit = settings.RATE_LIMIT_LOGIN if limit is None else limit
    eff_window = _LOGIN_RATE_WINDOW_S if window_sec is None else window_sec
    try:
        redis = get_redis()
        key = _ip_key(ip)
        # Atomic INCR; set TTL on first hit.
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, eff_window)
        return count <= eff_limit
    except Exception:
        # Redis unreachable → fall back to the in-process limiter rather
        # than failing fully open (account-level lockout still applies too).
        logger.warning("login IP rate-limit: Redis unavailable, using in-process fallback")
        return _local_allow(_ip_key(ip), eff_limit, eff_window)


def reset_ip_window(ip: str) -> None:
    """Clear the IP's counter (e.g. on successful login)."""
    if not ip:
        return
    try:
        get_redis().delete(_ip_key(ip))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Generic per-IP fixed window for non-login auth-adjacent endpoints
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
        logger.warning("%s IP rate-limit: Redis unavailable, using in-process fallback", bucket)
        return _local_allow(_bucket_key(bucket, ip), limit, window_sec)


# ---------------------------------------------------------------------------
# Per-account lockout
# ---------------------------------------------------------------------------


def is_account_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    return user.locked_until > utc_now()


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
    from . import settings_registry
    threshold = settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD)
    duration = timedelta(
        minutes=settings_registry.effective(db, settings_registry.K.LOCKOUT_DURATION_MIN)
    )
    now = utc_now()
    # Re-read user with a row-level write lock so concurrent record_failure
    # calls on the same row serialize.
    db.refresh(user, with_for_update=True)

    # A served lockout starts a fresh count. Only a SUCCESSFUL login used to
    # reset failed_login_count, so once an account had been locked the counter
    # stayed at the threshold forever: the next single wrong password re-locked
    # it immediately, and kept doing so. That turns a 15-minute lockout into a
    # permanent one for a user who mistypes, and lets anyone hold an account
    # locked indefinitely at one attempt per window (audit 2026-07-30).
    if user.locked_until is not None and user.locked_until <= now:
        user.failed_login_count = 0
        user.locked_until = None

    user.failed_login_count = (user.failed_login_count or 0) + 1
    just_locked = False
    if user.failed_login_count >= threshold and not is_account_locked(user):
        user.locked_until = now + duration
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
    user.last_login_at = utc_now()
    db.flush()


def mark_lockout_email_sent(db: Session, *, user: User) -> None:
    user.lockout_email_sent_at = utc_now()
    db.flush()
