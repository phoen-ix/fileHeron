"""Every five minutes: end secrets past their expiry and destroy their content,
and burn secrets nobody can read any more.

The reveal path checks expiry itself and burns a secret on its last view, so a
recipient never depends on this job. What depends on it is everything that
happens BETWEEN views: an unread secret must not outlive its expiry on disk, and
a secret whose readers left their group or were disabled must not sit there
unreadable until it expires. Idempotent - `end_secret` claims each secret with a
conditional UPDATE, so a reveal racing this sweep ends it exactly once.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import secret as secret_svc
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.expire_secrets")


@track_cron("expire_secrets")
async def expire_secrets(_ctx) -> dict:
    db = SessionLocal()
    try:
        expired = secret_svc.expire_due(db)
        db.commit()
        burned = secret_svc.sweep_exhausted(db)
        db.commit()
        if expired or burned:
            logger.info("expire_secrets: expired=%d burned=%d", expired, burned)
        return {"expired": expired, "burned": burned}
    finally:
        db.close()
