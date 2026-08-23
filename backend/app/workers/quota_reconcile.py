"""Hourly: reconcile Redis quota counters against DB sums.

The Redis counter at ``fh:quota:user:{id}`` is the source of truth for
the quota check in ``services/quota.py``. On Redis restart/flush the lazy
DB seed (``_initialize_from_db``) only runs on the next access, and two
reservations racing that first access can each seed from a sum that already
contains the other's in-flight row, so the counter drifts upward.

(This paragraph used to say the seed "only seeds from finalized files -
in-flight ``uploading`` rows aren't yet visible at seed time". That was
false: ``uploading`` is in ``STORED_STATES``. Believing it is what produced
the double-charge that refused a quota'd user's first large upload, and the
claim survived four releases because a docstring cannot carry a test.)

This job sums the DB authoritatively and overwrites the Redis counter
when drift exceeds ``_DRIFT_THRESHOLD`` (1 MiB). DB always wins.
"""
from __future__ import annotations

import logging

from sqlalchemy import func

from ..database import SessionLocal
from ..models.file import File
from ..models.user import User
from ..redis_client import eval_script, get_redis, sync
from ..services.cron_tracker import track_cron
from ..services.quota import STORED_STATES, _key

logger = logging.getLogger("fileheron.workers.quota_reconcile")

_DRIFT_THRESHOLD = 1024 * 1024  # 1 MiB

# Compare-and-set overwrite: only set the counter to db_sum if it STILL holds
# the value we read (ARGV[1]), or is still missing when it was missing on read
# (ARGV[3]=='0'). A plain GET-then-SET would clobber a concurrent
# reserve_bytes() INCRBY landing between our read and write, transiently
# under-enforcing quota (audit L35). On a mismatch we return 0 and skip - the
# next reconcile run picks it up.
_RECONCILE_CAS_LUA = """
local cur = redis.call('GET', KEYS[1])
if ARGV[3] == '0' then
    if cur == false then redis.call('SET', KEYS[1], ARGV[2]); return 1 end
    return 0
end
if cur == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2]); return 1 end
return 0
"""


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
                    File.state.in_(STORED_STATES),
                )
                .scalar()
                or 0
            )
            db_sum = int(db_sum)
            try:
                redis_val_raw = sync(redis.get(_key(user_id)))
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
                    # CAS overwrite (no TTL - the counter must not silently
                    # lapse to 0 between runs; Redis is persistent and this cron
                    # is the authority). Skips if a concurrent reservation moved
                    # the counter since our read (audit L35).
                    had = "1" if redis_val_raw is not None else "0"
                    expected = redis_val_raw if redis_val_raw is not None else ""
                    res = eval_script(
                        redis, _RECONCILE_CAS_LUA, 1, _key(user_id), expected, db_sum, had
                    )
                    if int(res) == 1:
                        fixed += 1
                    else:
                        logger.info(
                            "quota_reconcile: user=%d counter moved mid-reconcile; "
                            "skipping (next run reconciles)", user_id,
                        )
                except Exception as e:
                    logger.error(
                        "quota_reconcile: redis CAS failed user=%d: %s",
                        user_id, e,
                    )
        if fixed:
            logger.info("quota_reconcile: checked %d users, fixed %d", checked, fixed)
        return {"checked": checked, "fixed": fixed}
    finally:
        db.close()
