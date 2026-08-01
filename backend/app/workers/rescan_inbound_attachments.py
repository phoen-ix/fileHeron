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

import logging

from sqlalchemy import func

from ..database import SessionLocal
from ..models.inbound_attachment import AttachmentAVState, InboundAttachment
from ..services import av_scan
from ..services import storage_backend as storage_svc
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.rescan_inbound_attachments")

_BATCH = 200


_FAIL_KEY = "fh:inbound:rescan:fail:"
_FAIL_THRESHOLD = 5
_FAIL_TTL_SEC = 24 * 3600


def _record_failure(att_id: int) -> None:
    """Count a failed rescan so a permanently unscannable attachment stops
    consuming a slot. Fails open: no Redis, no deferral."""
    try:
        from ..redis_client import get_redis

        r = get_redis()
        key = f"{_FAIL_KEY}{att_id}"
        r.incr(key)
        r.expire(key, _FAIL_TTL_SEC)
    except Exception:
        pass


def _deferred_ids() -> set[int]:
    try:
        from ..redis_client import get_redis

        r = get_redis()
        out: set[int] = set()
        for key in r.scan_iter(match=f"{_FAIL_KEY}*", count=500):
            name = key.decode() if isinstance(key, bytes) else str(key)
            raw = r.get(name)
            count = int(raw) if raw is not None else 0
            if count >= _FAIL_THRESHOLD:
                out.add(int(name.rsplit(":", 1)[-1]))
        return out
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
            try:
                local = backend.local_path(att.storage_key)
                if local is not None:
                    result = av_scan.scan_path(local)
                else:
                    with backend.open(att.storage_key) as fh:
                        result = av_scan.scan_stream(fh)
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
