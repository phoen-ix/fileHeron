"""Live "is anything transferring right now" signals (v1.34.0).

Drives the postpone-update drain decision + the admin dialog. Two signals:

- **Downloads**: a Redis sorted set `fh:transfer:downloads` (member = per-download
  uuid, score = start epoch). A download adds itself when its byte stream starts and
  removes itself when it ends. Because a client disconnect mid-stream can skip the
  removal, `active_downloads()` first prunes members older than `MAX_DOWNLOAD_AGE_SEC`
  (definitely finished or leaked) - so a missed removal self-heals. This only tracks
  the local-filesystem backend; an S3 backend serves bytes via a 307 redirect the
  backend never sees (documented caveat).

- **Uploads**: the DB is the source - `files.state == uploading` (TUS). Direct
  uploads are synchronous and never linger in that state.

All Redis access is best-effort: a Redis outage degrades to "no active downloads"
rather than blocking an update (fail-open), mirroring `services/quota.py`.
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from ..redis_client import get_redis

logger = logging.getLogger("fileheron.transfer_activity")

_DOWNLOADS_KEY = "fh:transfer:downloads"
# An entry older than this is treated as finished/leaked and pruned. Generous so a
# genuinely long large-file download is never miscounted as gone; the drain worker's
# own max-wait cap is the real backstop.
MAX_DOWNLOAD_AGE_SEC = 6 * 3600


def _now() -> float:
    return time.time()


def download_started() -> str | None:
    """Register an in-flight download. Returns its id (pass to download_finished),
    or None if Redis is unavailable."""
    dl_id = uuid.uuid4().hex
    try:
        get_redis().zadd(_DOWNLOADS_KEY, {dl_id: _now()})
        return dl_id
    except Exception:
        logger.warning("transfer_activity: download_started failed (redis)")
        return None


def download_finished(dl_id: str | None) -> None:
    if not dl_id:
        return
    try:
        get_redis().zrem(_DOWNLOADS_KEY, dl_id)
    except Exception:
        logger.warning("transfer_activity: download_finished failed (redis)")


def active_downloads() -> int:
    """Count in-flight downloads, pruning definitely-done/leaked entries first."""
    try:
        r = get_redis()
        r.zremrangebyscore(_DOWNLOADS_KEY, 0, _now() - MAX_DOWNLOAD_AGE_SEC)
        return int(r.zcard(_DOWNLOADS_KEY))
    except Exception:
        logger.warning("transfer_activity: active_downloads failed (redis); assuming 0")
        return 0


def active_uploads(db: Session) -> int:
    """In-flight uploads: rows in `uploading` that plausibly still have bytes
    moving.

    An unqualified `COUNT(*) WHERE state = uploading` counted every abandoned
    row too. Rows only leave `uploading` via the tusd post-finish hook, so a
    browser tab closed mid-upload leaves one behind until
    `cleanup_abandoned_uploads` runs - up to TUS_UPLOAD_ABANDONED_AFTER_HOURS
    (24) later. The drain therefore almost never reached zero, the admin
    dialog showed permanent phantom activity, and a postponed update waited out
    its full deadline instead of firing when the stack was actually idle
    (audit 2026-07-30).

    Bounded by a freshness window tied to UPLOAD_STALE_AFTER_HOURS - the same
    knob the stale-upload sweeper uses, so the two agree on what "still going"
    means. The downloads counter is already self-healing via its age prune;
    this gives the uploads counter the same property.

    NOT filtered on `tus_upload_id IS NOT NULL`, which the finding suggested:
    `create_pending` sets state=uploading BEFORE tusd assigns an id, and a
    direct upload (POST /api/uploads/direct, up to 100 MB) never gets one at
    all. That filter would have made every direct upload invisible to the
    drain - the same blind spot as the unregistered preview streams fixed
    alongside this. The repo's own test_maintenance.py caught it."""
    from datetime import timedelta

    from ..config import settings
    from ..models.file import File, FileState
    from ..utils.timeutil import utc_now

    cutoff = utc_now() - timedelta(hours=max(1, settings.UPLOAD_STALE_AFTER_HOURS))
    return int(
        db.query(File)
        .filter(
            File.state == FileState.uploading,
            File.created_at > cutoff,
        )
        .count()
    )


def snapshot(db: Session) -> dict:
    return {
        "active_uploads": active_uploads(db),
        "active_downloads": active_downloads(),
    }
