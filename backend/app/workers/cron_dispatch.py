"""Minute dispatcher for admin-tunable cron schedules (v1.28.0).

Ticks every minute; for each registered cron whose admin-configured schedule is
due, enqueues the job (which still runs under its own ``@track_cron``). This
replaces the static per-job ARQ ``cron(...)`` entries so cadence/enable/disable
are runtime-editable (see ``services/cron_schedule.py``).

Per-job try/except: one job's evaluation failing never blocks the others. Not
itself ``@track_cron`` - it runs 1440x/day and would flood ``cron_runs``; it logs
instead, and a real dispatch failure surfaces via the worker logs.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import cron_schedule as cs
from ..services import job_queue
from ..services import site as site_svc
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.cron_dispatch")


async def cron_dispatch(_ctx) -> dict:
    db = SessionLocal()
    enqueued: list[str] = []
    try:
        tz = site_svc.get_site_timezone(db)
        now = utc_now()
        for name in cs.REGISTRY:
            try:
                res = cs.effective(db, name)
                if not res.enabled:
                    continue
                last = cs.get_last_run(db, name)
                if last is None:
                    # First sight: seed the clock so jobs don't all fire at once
                    # on startup. Interval jobs run after one interval; daily jobs
                    # run at their next configured time.
                    cs.mark_ran(db, name, now)
                    db.commit()
                    continue
                if cs.is_due(res, last, now, tz):
                    await job_queue.aenqueue(name)
                    cs.mark_ran(db, name, now)
                    db.commit()
                    enqueued.append(name)
            except Exception:
                db.rollback()
                logger.exception("cron_dispatch: failed for %s", name)
        if enqueued:
            logger.info("cron_dispatch enqueued: %s", enqueued)
        return {"enqueued": enqueued}
    finally:
        db.close()
