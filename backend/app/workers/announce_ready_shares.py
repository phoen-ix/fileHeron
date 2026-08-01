"""Announce a share whose uploads have finished but whose client never said so.

`share_created` is deferred until the files land - a share is empty at create
time, so announcing there told every recipient "0 files" (audit #2). The owner's
batch-complete signal announces immediately; this sweep is the fallback for a
client that sends no such signal (an API-token integration, or a browser tab
closed mid-batch).

It runs every minute and requires QUIET: nothing uploading, and nothing new for
`ANNOUNCE_QUIET_SECONDS`. Every shipped client uploads sequentially, so "nothing
is uploading right now" is momentarily true between two files - announcing there
would have said "1 file" for a three-file share.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.file import File
from ..models.share import Share, ShareState
from ..services import share as share_svc
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.announce_ready_shares")

# Nothing older than this is worth chasing: a share whose uploads never
# finished is handled by cleanup_stale_uploads, which flips it to `failed`.
_LOOKBACK_HOURS = 48


@track_cron("announce_ready_shares")
async def announce_ready_shares(_ctx) -> dict:
    db = SessionLocal()
    sent = 0
    try:
        candidates = (
            db.query(Share.id)
            .join(File, File.share_id == Share.id)
            .filter(
                Share.state == ShareState.active,
                Share.notify_on_activation.isnot(None),
                Share.created_at > utc_now() - timedelta(hours=_LOOKBACK_HOURS),
            )
            .distinct()
            .limit(200)
            .all()
        )
        for (share_id,) in candidates:
            try:
                if share_svc.announce_if_ready(db, share_id, require_quiet=True):
                    db.commit()
                    sent += 1
                else:
                    db.rollback()
            except Exception:
                db.rollback()
                logger.exception("announce sweep failed for share=%s", share_id)
        if sent:
            logger.info("announce_ready_shares: announced %d share(s)", sent)
        return {"announced": sent, "candidates": len(candidates)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
