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


# Per-file "this instance served bytes for it recently" marks.
#
# The maintenance gate lets a `Range:` continuation through, on the reasoning
# that it is finishing an in-progress transfer rather than starting a new one.
# That was taken purely from the SHAPE of the header, so `Range: bytes=1-` on a
# brand-new connection bypassed the gate entirely (audit 2026-07-30, config-7).
# A mark recorded when a download actually starts turns the claim into something
# checkable.
#
# The window is deliberately short. Maintenance is a drain: a transfer that has
# been idle for an hour is a NEW transfer, and refusing it is correct.
_RECENT_KEY_PREFIX = "fh:transfer:recent:"
RECENT_DOWNLOAD_TTL_SEC = 30 * 60


def mark_download_recent(file_id: str) -> None:
    """Record that this instance just started serving `file_id`."""
    try:
        get_redis().set(
            f"{_RECENT_KEY_PREFIX}{file_id}", "1", ex=RECENT_DOWNLOAD_TTL_SEC
        )
    except Exception:
        logger.warning("transfer_activity: recent-mark failed (redis)")


def was_download_recent(file_id: str) -> bool:
    """Whether this instance served bytes for `file_id` inside the window.

    Fails OPEN: with Redis unreachable we cannot tell a resume from a fabricated
    range, and refusing a genuine resume is the worse outcome - the same
    fail-open posture the quota counter takes."""
    try:
        return get_redis().get(f"{_RECENT_KEY_PREFIX}{file_id}") is not None
    except Exception:
        logger.warning("transfer_activity: recent-check failed (redis); allowing")
        return True


def download_started(file_id: str | None = None) -> str | None:
    """Register an in-flight download. Returns its id (pass to download_finished),
    or None if Redis is unavailable."""
    if file_id:
        mark_download_recent(file_id)
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


# --- the PAYMENT mark, which is a different question -------------------------
#
# `was_download_recent` answers "did this instance serve bytes for this thing
# recently". That is the right question for the maintenance drain, and the wrong
# one for a download budget, which needs "has THIS CALLER already paid for it".
# v2.6.0 used the serving mark for both, and the difference was reachable:
#
# - the share owner previewing their own file wrote the mark, so every holder of
#   the public link got unlimited free copies until it expired;
# - the authenticated ZIP and the public ZIP derived an identical key from the
#   same reproducible archive identity, so one corroborated the other across the
#   auth boundary;
# - and because the mark was written wherever bytes were served, a FREE
#   continuation refreshed it - so the window renewed itself indefinitely, while
#   the comment beside it said "Bounded, unlike unlimited-forever".
#
# So the budget gets its own mark, written only where the counter actually moves
# and namespaced by the principal that moved it. One principal's activity can no
# longer corroborate another's, and a free continuation cannot extend its own
# licence because it never reaches the payment path.
_PAID_KEY_PREFIX = "fh:transfer:paid:"


# The payment mark's own window. RECENT_DOWNLOAD_TTL_SEC (30 min) is the SERVING
# mark's TTL and is far too short here: a 9 GB archive on a 25 Mbit/s line takes
# 50 minutes, so the mark expired mid-transfer and the resume was answered 410
# PUBLIC_LINK_EXHAUSTED - the exact outcome flow-publiclink-5 was filed for and
# the route docstring claims is fixed (audit #2). Matched to the authenticated
# path's resume credit, which is measured in hours for the same reason.
# Long enough for the transfer it exists to protect - a 9 GB archive on a
# 25 Mbit/s line is ~50 minutes - and no longer. It is not a free-download
# window: a continuation must start past byte 0, so a full fetch still pays.
# But `Range: bytes=1-` is very nearly the whole file, so every extra hour here
# is an hour in which one paid download can be repeated (audit #2 cross-check
# flagged 12 h as a day pass; the 30 minutes it replaced was too short to finish
# the transfer at all).
PAID_TTL_SEC = 2 * 3600


def mark_download_paid(principal_key: str) -> None:
    """Record that `principal_key` just PAID for a transfer.

    Call this where the counter is decremented, never where bytes are served.
    `principal_key` must identify the payer as well as the thing paid for -
    e.g. `link:{link_id}:file:{file_id}`."""
    try:
        get_redis().set(f"{_PAID_KEY_PREFIX}{principal_key}", "1", ex=PAID_TTL_SEC)
    except Exception:
        logger.warning("transfer_activity: paid-mark failed (redis)")


def was_download_paid(principal_key: str) -> bool:
    """Whether `principal_key` paid inside the window.

    Fails CLOSED, unlike the serving mark. The serving mark answers "is a
    transfer in flight", where a wrong answer costs a paused download; this one
    answers "has this caller already paid", where a wrong answer costs the
    budget itself. Failing open meant that for the duration of a Redis outage a
    public link with `downloads_remaining = 0` served the complete archive to
    anyone sending `Range: bytes=1-`, repeatedly, with no counter movement, no
    download_log row and nothing in the owner's history (audit #2).

    The cost of failing closed is that a genuine resume during an outage pays a
    second download rather than being free. That is recoverable; unlimited free
    extraction is not.
    """
    try:
        return get_redis().get(f"{_PAID_KEY_PREFIX}{principal_key}") is not None
    except Exception:
        logger.warning("transfer_activity: paid-check failed (redis); charging")
        return False
