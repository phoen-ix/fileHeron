"""Cron: re-scan inbound attachments left in `pending` (audit L18).

Inbound attachments are AV-scanned inline at ingest (services/inbound_mail.py).
If ClamAV was unavailable (its documented slow first boot, an outage) or returned
an inconclusive result, the attachment is stored `pending` and gated from the
admin download endpoint. Without a recovery path it would stay pending - and
undownloadable - forever (the model docstring promised a `scan_inbound_attachment`
job that never existed).

This sweep re-scans pending attachments and settles them to clean/infected. It
leaves them pending (to retry next run) when ClamAV is still unavailable or the
result is inconclusive, and bails out early once clamd is unreachable so it
doesn't hammer a dead daemon. Idempotent: a clean/infected row is never revisited.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func

from ..database import SessionLocal
from ..models.inbound_attachment import AttachmentAVState, InboundAttachment
from ..redis_client import get_redis, sync
from ..services import av_scan
from ..services import storage_backend as storage_svc
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now_aware

logger = logging.getLogger("fileheron.workers.rescan_inbound_attachments")

_BATCH = 200


_FAIL_KEY = "fh:inbound:rescan:fail:"
_FAIL_THRESHOLD = 5
_FAIL_TTL_SEC = 24 * 3600
# Attachments at the threshold, scored by the time of their latest failure.
# A FIXED key the reader can ask directly: finding them used to be a SCAN over
# `fh:inbound:rescan:fail:*`, and this Redis may be shared - walking a keyspace
# under another tenant's load is the latency tax scan_guard.py refuses for the
# same reason. Retention is per member (pruned by score, like scan_guard's
# watchlist), so an attachment is forgiven 24h after its last failure, as the
# counter's own TTL always did; the key's EXPIRE only collects an idle set.
_DEFERRED_KEY = "fh:inbound:rescan:deferred"


def _record_failure(att_id: int) -> None:
    """Count a failed rescan so a permanently unscannable attachment stops
    consuming a slot. Fails open: no Redis, no deferral."""
    try:
        r = get_redis()
        key = f"{_FAIL_KEY}{att_id}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _FAIL_TTL_SEC)
        count = int(pipe.execute()[0])
        if count >= _FAIL_THRESHOLD:
            pipe = r.pipeline()
            pipe.zadd(_DEFERRED_KEY, {str(att_id): utc_now_aware().timestamp()})
            pipe.expire(_DEFERRED_KEY, _FAIL_TTL_SEC)
            pipe.execute()
    except Exception:
        pass


def _deferred_ids() -> set[int]:
    try:
        r = get_redis()
        cutoff = utc_now_aware().timestamp() - _FAIL_TTL_SEC
        r.zremrangebyscore(_DEFERRED_KEY, "-inf", f"({cutoff}")
        members = sync(r.zrange(_DEFERRED_KEY, 0, -1))
        return {int(m.decode() if isinstance(m, bytes) else m) for m in members}
    except Exception:
        return set()


@track_cron("rescan_inbound_attachments")
async def rescan_inbound_attachments(_ctx) -> dict:
    db = SessionLocal()
    clean = infected = still_pending = 0
    try:
        # RANDOM order, not the table's natural one. `pending` has no terminal
        # state for an attachment that can never be scanned - a blob lost to a
        # storage incident scans as `error` and stays pending forever - so an
        # unordered LIMIT re-selected the same dead rows every hour and settled
        # nothing. Legitimate attachments queued behind them (after a clamd
        # outage, say) were never reached and stayed permanently
        # un-downloadable, with no admin-visible explanation (audit #2).
        # Random ordering gives every pending row a turn; `_deferred` then stops
        # a persistently failing one from consuming a slot at all.
        deferred = _deferred_ids()
        q = db.query(InboundAttachment).filter(
            InboundAttachment.av_state == AttachmentAVState.pending
        )
        if deferred:
            q = q.filter(InboundAttachment.id.notin_(list(deferred)[:1000]))
        pending = q.order_by(func.random()).limit(_BATCH).all()
        if not pending:
            return {"rescanned": 0, "clean": 0, "infected": 0, "still_pending": 0}

        backend = storage_svc.get_storage_backend()
        for att in pending:
            # Local backend -> path-scan (clamd reads the shared mount); object
            # store -> stream the bytes via INSTREAM. Same choice as av_scan_file.
            # Both scan paths are BLOCKING socket I/O and this is an `async
            # def` on the ARQ worker's single event loop, so a slow clamd froze
            # every other job in the process - send_email, webhook_deliver, and
            # cron_dispatch itself, which is the every-minute tick that drives
            # all the others. With _BATCH = 200 and SOCKET_TIMEOUT_SEC = 1800
            # the worst case is 100 hours of frozen loop, and job_timeout cannot
            # cut it short: asyncio.wait_for cannot pre-empt a blocking recv.
            #
            # workers/av_scan.py:160-172 already does this correctly, with a
            # comment describing this exact failure - the 2026-07-30 fix was
            # applied to one of the two call sites.
            def _scan(key: str = att.storage_key) -> av_scan.ScanResult:
                local = backend.local_path(key)
                if local is not None:
                    return av_scan.scan_path(local)
                with backend.open(key) as fh:
                    return av_scan.scan_stream(fh)

            try:
                result = await asyncio.to_thread(_scan)
            except av_scan.AVUnavailableError:
                logger.warning(
                    "rescan_inbound_attachments: clamd unavailable; deferring "
                    "remaining %d attachment(s) to next run", len(pending),
                )
                break
            except Exception as e:
                logger.error(
                    "rescan_inbound_attachments: read/scan failed att=%s: %s", att.id, e
                )
                _record_failure(att.id)
                still_pending += 1
                continue

            if result.state == "clean":
                att.av_state = AttachmentAVState.clean
                clean += 1
            elif result.state == "infected":
                att.av_state = AttachmentAVState.infected
                infected += 1
            else:
                # Inconclusive ('error') - leave pending and retry next run.
                _record_failure(att.id)
                still_pending += 1

        db.commit()
        if clean or infected or still_pending:
            logger.info(
                "rescan_inbound_attachments: clean=%d infected=%d still_pending=%d",
                clean, infected, still_pending,
            )
        return {
            "rescanned": clean + infected,
            "clean": clean,
            "infected": infected,
            "still_pending": still_pending,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
