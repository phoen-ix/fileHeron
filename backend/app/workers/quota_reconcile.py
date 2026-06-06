"""Hourly: reconcile Redis quota counters against DB sums.

The Redis counter at ``fh:quota:user:{id}`` is the source of truth for
the quota check in ``services/quota.py``. On Redis restart/flush, the
lazy DB seed (``_initialize_from_db``) only runs on next access AND only
seeds from finalized files - in-flight ``uploading`` rows aren't yet
visible at seed time in many race orderings, so the counter under-reports
the user's true commitment.

This job sums the DB authoritatively and overwrites the Redis counter
when drift exceeds ``_DRIFT_THRESHOLD`` (1 MiB). DB always wins.
"""
from __future__ import annotations

import logging

from sqlalchemy import func

from ..database import SessionLocal
from ..models.file import File, FileState
from ..models.user import User
from ..redis_client import get_redis
from ..services.cron_tracker import track_cron
from ..services.quota import _key

logger = logging.getLogger("fileheron.workers.quota_reconcile")

_DRIFT_THRESHOLD = 1024 * 1024  # 1 MiB


@track_cron("quota_reconcile")
async def quota_reconcile(_ctx) -> dict:
    db = SessionLocal()
    checked = 0
    fixed = 0
    try:
        try:
            redis = get_redis()
        except Exception:
            logger.warning("quota_reconcile: redis unreachable, skipping")
            return {"checked": 0, "fixed": 0}

        users = db.query(User.id).all()
        for (user_id,) in users:
            checked += 1
            db_sum = (
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
            db_sum = int(db_sum)
            try:
                redis_val_raw = redis.get(_key(user_id))
            except Exception:
                continue
            redis_val = int(redis_val_raw) if redis_val_raw else 0
            # Fix on meaningful drift, OR whenever the counter went negative
            # (release-without-reserve), regardless of magnitude.
            if abs(db_sum - redis_val) > _DRIFT_THRESHOLD or redis_val < 0:
                logger.warning(
                    "quota_reconcile: user=%d drift=%d (db=%d redis=%d) → DB wins",
                    user_id, abs(db_sum - redis_val), db_sum, redis_val,
                )
                try:
                    # No TTL - the counter must not silently lapse to 0 between
                    # reconcile runs (Redis is persistent; this cron is the
                    # authority).
                    redis.set(_key(user_id), db_sum)
                    fixed += 1
                except Exception as e:
                    logger.error(
                        "quota_reconcile: redis set failed user=%d: %s",
                        user_id, e,
                    )
        if fixed:
            logger.info("quota_reconcile: checked %d users, fixed %d", checked, fixed)
        return {"checked": checked, "fixed": fixed}
    finally:
        db.close()
