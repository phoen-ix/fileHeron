"""Minute dispatcher for admin-tunable cron schedules (v1.28.0).

Ticks every minute; for each registered cron whose admin-configured schedule is
due, enqueues the job (which still runs under its own ``@track_cron``). This
replaces the static per-job ARQ ``cron(...)`` entries so cadence/enable/disable
are runtime-editable (see ``services/cron_schedule.py``).

Per-job try/except: one job's evaluation failing never blocks the others. Not
itself ``@track_cron`` - it runs 1440x/day and would flood ``cron_runs``. A task
it cannot dispatch raises the `cron_failing` alert instead (at most one an
hour): logged only, such a task silently stopped running.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import admin_alerts, job_queue
from ..services import cron_schedule as cs
from ..services import site as site_svc
from ..services.cron_tracker import exc_summary
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.cron_dispatch")


def _alarm(db, name: str, exc: Exception) -> None:
    """Tell the admins a task could not be dispatched. Never raises: the loop
    goes on to the other tasks either way."""
    try:
        admin_alerts.ops_alert(
            db,
            reason="cron_failing",
            detail=f"{name} could not be dispatched: {exc_summary(exc)}",
            link_url="/admin/scheduled-tasks",
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("cron_dispatch: could not raise the alarm for %s", name)


async def cron_dispatch(_ctx) -> dict:
    db = SessionLocal()
    enqueued: list[str] = []
    try:
        tz = site_svc.get_site_timezone(db)
        now = utc_now()
        # One snapshot per tick (two SELECTs), not `effective` + `get_last_run`
        # per registry entry (three each, ~70 a minute for 23 jobs) - the same
        # two-query read the admin Scheduled-tasks page uses (audit 2026-11-08).
        snap = cs.snapshot(db)
        for name in cs.REGISTRY:
            try:
                res = snap.schedules[name]
                if not res.enabled:
                    continue
                last = snap.last_runs[name]
                if last is None:
                    # First sight: seed the clock so jobs don't all fire at once
                    # on startup. Interval jobs run after one interval; daily jobs
                    # run at their next configured time.
                    cs.mark_ran(db, name, now)
                    db.commit()
                    continue
                if cs.is_due(res, last, now, tz):
                    # Persist "ran" BEFORE enqueuing (audit Info-11): if the
                    # commit fails we simply retry next minute, instead of having
                    # enqueued the job but not recorded it - which re-fires it
                    # every minute until a commit lands, widening the overlap
                    # window. All cron jobs are idempotent, so the rare
                    # marked-but-enqueue-failed case just skips one run.
                    cs.mark_ran(db, name, now)
                    db.commit()
                    await job_queue.aenqueue(name)
                    enqueued.append(name)
            except Exception as exc:
                db.rollback()
                logger.warning("cron_dispatch: failed for %s", name, exc_info=True)
                _alarm(db, name, exc)
        if enqueued:
            logger.info("cron_dispatch enqueued: %s", enqueued)
        return {"enqueued": enqueued}
    finally:
        db.close()
