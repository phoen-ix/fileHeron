"""Redis client. Lazy connection pool initialized on first call.

Used by:
- services/rate_limit.py - login attempt sliding window
- services/auth.py - (Phase 1b indirect) lockout cache misses fall back to Redis
- Phase 4+: ARQ jobs.
"""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, cast

import redis

from .config import settings


def sync[T](value: Awaitable[T] | T) -> T:
    """Drop the `Awaitable` arm redis-py puts on every command's return type.

    redis-py shares one commands mixin between its sync and async clients, so
    every method is typed `Awaitable[T] | T` regardless of which client you
    hold. `get_redis()` returns the SYNC client, which never returns an
    awaitable - so this is a cast, not a conversion, and compiles to nothing.

    One definition rather than a cast at each of the ~15 call sites, and a
    place to record why the union is wrong here.
    """
    return cast(T, value)


def eval_script(client: redis.Redis, script: str, numkeys: int, *keys_and_args: object) -> Any:
    """Run a Lua script without redis-py's two stub inaccuracies getting in the way.

    `eval` is typed as taking `str` arguments and returning `str`. Neither
    holds: the encoder accepts ints (it writes their decimal form, which is
    what `tonumber(ARGV[n])` reads), and a script returning a Lua table comes
    back as a list. The call is deliberately made through an untyped alias so
    the ARGUMENTS reach redis-py exactly as the caller passed them - the quota
    reservation path is atomic and must not have its values rewritten by a
    typing fix.
    """
    return cast(Any, client.eval)(script, numkeys, *keys_and_args)


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
