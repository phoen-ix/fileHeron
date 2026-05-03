"""Redis client. Lazy connection pool initialized on first call.

Used by:
- services/rate_limit.py — login attempt sliding window
- services/auth.py — (Phase 1b indirect) lockout cache misses fall back to Redis
- Phase 4+: ARQ jobs.
"""
from __future__ import annotations

import redis

from .config import settings

_pool: redis.ConnectionPool | None = None


def get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _pool


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=get_pool())
