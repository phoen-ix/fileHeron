"""Per-user storage quota — Redis-backed atomic INCRBY-with-bound.

Counter key: ``fh:quota:user:{user_id}`` holds the user's currently-allocated
bytes (sum of in-flight + finalized files that haven't been deleted).

Lifecycle:
- pre-create: reserve_bytes(user, announced_size). Rejected if over quota.
- post-terminate / file-delete: release_bytes(user, size). Frees the reservation.
- post-finish: no quota op (already reserved at pre-create).

The counter is initialized lazily on first access from the DB SUM. There's
a small race window where two simultaneous first-access uploads could each
see "no key" and both seed it, but they both seed to the same value (the
current DB sum), so the result is consistent. Any reservations they made
before seeding are still INCRBY'd correctly.
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.file import File, FileState
from ..models.user import User
from ..redis_client import get_redis

logger = logging.getLogger("fileheron.quota")

# Lua: GET key (default 0), if (limit > 0 AND current+size > limit) → return -1.
# Else INCRBY size and return new total. All atomic, no race.
_RESERVE_LUA = """
local key = KEYS[1]
local size = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local current = tonumber(redis.call('GET', key) or '0')
local new_total = current + size
if limit > 0 and new_total > limit then
    return -1
end
redis.call('INCRBY', key, size)
return new_total
"""


def _key(user_id: int) -> str:
    return f"fh:quota:user:{user_id}"


def _initialize_from_db(db: Session, user_id: int) -> int:
    """Sum the user's currently-allocated bytes from the DB. Sets the Redis
    counter via SET NX so concurrent initializers don't double-write."""
    sum_q = (
        db.query(func.coalesce(func.sum(File.size_bytes), 0))
        .filter(
            File.uploaded_by_id == user_id,
            File.state.in_(
                [FileState.uploading, FileState.ready_unscanned, FileState.clean]
            ),
        )
        .scalar()
        or 0
    )
    used = int(sum_q)
    try:
        get_redis().set(_key(user_id), used, nx=True, ex=86400)
    except Exception:
        # Redis unreachable — quota check will fail-open below.
        logger.warning("redis unreachable during quota init for user %d", user_id)
    return used


def reserve_bytes(db: Session, *, user: User, additional_bytes: int) -> int:
    """Atomically reserve ``additional_bytes`` against the user's quota.
    Returns the new allocated total. Raises AppError(413, QUOTA_EXCEEDED).

    If Redis is unreachable, this fails OPEN — the upload is allowed.
    Quota is a "user fairness" control, not an "absolute storage cap".
    The hourly `workers/quota_reconcile.py` cron rebuilds the Redis
    counter from the DB sum on a schedule so drift never compounds
    invisibly.
    """
    if additional_bytes < 0:
        raise AppError(400, "INVALID_SIZE", "Negative reservation.")

    quota_limit = user.quota_bytes if user.quota_bytes is not None else 0  # 0 = unlimited
    try:
        redis = get_redis()
        if not redis.exists(_key(user.id)):
            _initialize_from_db(db, user.id)

        result = redis.eval(_RESERVE_LUA, 1, _key(user.id), additional_bytes, quota_limit)
        new_total = int(result)
        if new_total == -1:
            raise AppError(
                413,
                "QUOTA_EXCEEDED",
                "This upload would exceed your storage quota.",
                details={"quota_bytes": user.quota_bytes},
            )
        return new_total
    except AppError:
        raise
    except Exception as e:
        logger.warning("quota fail-open due to redis: %s", e)
        return additional_bytes


def release_bytes(*, user_id: int, bytes_to_free: int) -> None:
    """Decrement the Redis counter when an upload is abandoned (post-terminate)
    or a file is deleted. Best-effort — if Redis is down, we accept the
    counter will drift; the hourly `workers/quota_reconcile.py` cron
    repairs drift > 1 MiB."""
    if bytes_to_free <= 0:
        return
    try:
        get_redis().decrby(_key(user_id), bytes_to_free)
    except Exception:
        logger.warning("quota release failed (redis): user=%d bytes=%d", user_id, bytes_to_free)


def used_bytes(*, user_id: int) -> int:
    """Read-only — returns the current Redis counter or 0 if uninitialized."""
    try:
        v = get_redis().get(_key(user_id))
        return int(v) if v else 0
    except Exception:
        return 0
