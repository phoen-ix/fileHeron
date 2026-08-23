"""Drain-before-update worker (v1.34.0).

Runs every minute. When a postponed self-update is pending (the admin chose
"postpone" in the Update dialog, which set maintenance mode + a
`maintenance.pending_update` record), this fires the update once in-flight
transfers have drained - or once the max-wait deadline has passed, so a single
paused/stuck resumable transfer can't block the update forever.

Idempotent: with no pending record it no-ops; once it triggers the update it
clears both the pending record and maintenance mode (so the freshly-updated
container doesn't boot stuck in maintenance).
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import maintenance as maintenance_svc
from ..services import transfer_activity as ta
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.drain_pending_update")


async def drain_pending_update(_ctx) -> dict:
    """Untracked outer shell.

    This worker runs every minute, so wrapping the whole thing in @track_cron
    wrote 1440 `cron_runs` rows a day - which then evicted its own failure rows
    from any retention window inside about three hours, so the one run that
    mattered was gone before anyone looked. The dispatcher itself is
    deliberately not tracked for exactly this reason
    (cron_schedule: "1440x/day would flood cron_runs").

    Nothing is pending on the overwhelming majority of ticks, so check that
    first and only enter the tracked body when there is real work
    (audit 2026-07-30)."""
    db = SessionLocal()
    try:
        if maintenance_svc.get_pending_update(db) is None:
            # No pending update, but maintenance may still be held shut by a
            # hand-off that never produced a new container (the executor died,
            # the pull failed). The new backend clears the gate on boot; this
            # is the other end of that, so a failed update cannot leave the
            # instance refusing transfers forever (flow-maintenance-5).
            return {"pending": False, "lifted": _lift_if_stale(db)}
    finally:
        db.close()
    return await _drain_pending_update_tracked(_ctx)


def _lift_if_stale(db) -> bool:
    stamp = maintenance_svc.get_handoff_at(db)
    if not stamp:
        return False
    try:
        from datetime import datetime, timedelta

        handed_off = datetime.fromisoformat(stamp)
    except ValueError:
        return maintenance_svc.clear_maintenance_after_update(db)
    if utc_now() - handed_off < timedelta(minutes=maintenance_svc.HANDOFF_STALE_MIN):
        return False
    logger.warning(
        "update hand-off at %s produced no new container within %d min; "
        "lifting maintenance", stamp, maintenance_svc.HANDOFF_STALE_MIN,
    )
    return maintenance_svc.clear_maintenance_after_update(db)


@track_cron("drain_pending_update")
async def _drain_pending_update_tracked(_ctx) -> dict:
    db = SessionLocal()
    try:
        pending = maintenance_svc.get_pending_update(db)
        if not pending:
            return {"pending": False}

        active_uploads = ta.active_uploads(db)
        # None = Redis could not answer. `None == 0` is False, so an unknown
        # count is NOT drained and this waits - bounded by the deadline below.
        active_downloads = ta.active_downloads()
        drained = active_uploads == 0 and active_downloads == 0

        past_deadline = False
        deadline = pending.get("deadline_iso")
        if deadline:
            try:
                from datetime import datetime

                past_deadline = utc_now() >= datetime.fromisoformat(deadline)
            except ValueError:
                past_deadline = True  # unparseable deadline -> don't wedge forever

        if not (drained or past_deadline):
            return {
                "pending": True,
                "triggered": False,
                "active_uploads": active_uploads,
                "active_downloads": active_downloads,
            }

        reason = "drain" if drained else "deadline"
        result = maintenance_svc.apply_pending_update(db, reason=reason)
        logger.info(
            "drain_pending_update: fired update via=%s (uploads=%d downloads=%s)",
            reason, active_uploads, active_downloads,
        )
        return {
            "pending": True,
            "triggered": True,
            "reason": reason,
            "job_id": (result or {}).get("job_id"),
        }
    finally:
        db.close()
