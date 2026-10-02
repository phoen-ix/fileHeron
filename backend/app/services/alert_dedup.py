"""One dedup for the alerts the scheduled checks send admins.

disk_check, anomaly_check, ops_check and cron_tracker each answered "did I
already alert about this within the hour?" with their own exists-then-set
against Redis, and each returned "not seen" when Redis raised - so during a
Redis outage every hourly run re-sent every alert, `ops_check`'s own
`redis_unhealthy` among them, and anomaly_check wrote a duplicate audit row per
run. Treating the outage as "already sent" instead would have silenced the alert
that Redis itself is down.

So: an atomic `SET key NX EX ttl` (the old exists-then-set could let two
callers both alert), and when Redis raises, an in-process record answers - the
same fallback shape `rate_limit._local_allow` uses for the login limiter. It is
per worker process, which is where these checks run, so an outage costs at most
one alert per key per process per window instead of one per run.
"""
from __future__ import annotations

import logging
import threading
import time

from ..redis_client import get_redis

logger = logging.getLogger("fileheron.alert_dedup")

_local_lock = threading.Lock()
_local_until: dict[str, float] = {}  # key -> monotonic expiry
_LOCAL_PRUNE_ABOVE = 4096


def _local_seen(key: str, ttl_sec: int) -> bool:
    now = time.monotonic()
    with _local_lock:
        until = _local_until.get(key)
        if until is not None and until > now:
            return True
        _local_until[key] = now + ttl_sec
        if len(_local_until) > _LOCAL_PRUNE_ABOVE:
            for k in [k for k, u in _local_until.items() if u <= now]:
                _local_until.pop(k, None)
    return False


def seen_recently(key: str, ttl_sec: int) -> bool:
    """True if `key` already fired within `ttl_sec`; otherwise records it and
    returns False, so the caller sends exactly once per window."""
    try:
        return not get_redis().set(key, "1", nx=True, ex=ttl_sec)
    except Exception:
        logger.warning("alert dedup: redis unavailable, using the in-process record for %s", key)
        return _local_seen(key, ttl_sec)


def forget(key: str) -> None:
    """Drop `key` from both records, so the next occurrence alerts at once."""
    try:
        get_redis().delete(key)
    except Exception:
        pass
    with _local_lock:
        _local_until.pop(key, None)


def _reset_local() -> None:
    """Tests only: the in-process record outlives a test otherwise."""
    with _local_lock:
        _local_until.clear()
