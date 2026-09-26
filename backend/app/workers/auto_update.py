"""Automatic updates - the daily `auto_update` task (services/auto_update.py).

A no-op unless an admin switched automatic updates on. Otherwise it schedules
the eligible release through the postpone path and leaves the rest to
`drain_pending_update`. Its time is editable on Scheduled tasks (default 03:30,
site timezone).

Never raises: a failure is reported through CRON_FAILED_KEY, like
release_check, because a raise would be retried `max_tries` times.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import auto_update as auto_update_svc
from ..services.cron_tracker import CRON_FAILED_KEY, track_cron

logger = logging.getLogger("fileheron.workers.auto_update")


@track_cron("auto_update")
async def auto_update(_ctx) -> dict:
    db = SessionLocal()
    try:
        return auto_update_svc.schedule(db)
    except Exception as exc:
        db.rollback()
        logger.exception("automatic update: scheduling failed")
        return {CRON_FAILED_KEY: True, "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        db.close()
