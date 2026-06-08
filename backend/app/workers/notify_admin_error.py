"""ARQ job: email admins about a server error (HTTP 5xx or a failed cron).

Event-driven (enqueued by the exception handlers + cron_tracker), NOT a tracked
cron - deliberately, so a failure in the alert path can't feed itself as a new
"cron failed" error (no self-amplification). All saferails + send logic live in
``services/error_alert.py``; this is the thin worker shell that owns the DB
session. Best-effort: it logs and returns rather than raising.
"""
from __future__ import annotations

import logging
from typing import Any

from ..database import SessionLocal
from ..services import error_alert

logger = logging.getLogger("fileheron.workers.notify_admin_error")


async def notify_admin_error(_ctx, *, event: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return error_alert.handle_error_event(db, event)
    except Exception:
        logger.exception("notify_admin_error: unexpected failure")
        return {"status": "error"}
    finally:
        db.close()
