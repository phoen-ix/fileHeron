"""Encryption at rest - the lanes that move stored bytes into ciphertext.

THE RELEASE LANE encrypts a new upload before it is released.

With `storage.encrypt_at_rest` on, a scanned file is not flipped to `clean` by
the scan. `av_scan_file` records the verdict in `files.release_verdict` and
leaves the row `ready_unscanned` (still answering 425) - then this lane writes
the ciphertext and flips the row to `clean` AND onto the ciphertext in ONE
conditional UPDATE (`av_release.apply_verdict` with the swap folded in). So
there is never a moment where the file is downloadable and plaintext, and the
recipients are mailed only after the swap.

`ready_unscanned` must stay terminal: ANY failure to encrypt - no space, an
unreadable source, a fault of the storage - releases the file as plaintext with
a `file_encryption_deferred` audit row, and the backfill encrypts it later. The
one exception is a worker shutdown: the verdict stays recorded and the next run
(re-kicked by the scan sweep or the backfill) picks it up.

One lane runs at a time across the stack (a Redis lock, renewed while held), so
a burst of uploads is encrypted in arrival order instead of all at once. Every
kick marks the lane dirty BEFORE trying the lock; the holder clears the mark
before it reads the queue and checks it again after it lets go - so a verdict
that lands while the lane is busy is never left behind by the hand-over.

THE BACKFILL (cron `encrypt_existing_files`) encrypts what is already stored:
clean and quarantined files, and inbound mail attachments, one at a time
within a time budget. The plaintext it replaces is purged an hour later, so a
ZIP stream that opened its member list before the swap still finds it. A file
served in the last half hour is left for a later run, and a file that fails
three times is deferred for a day (an admin can clear that). Each run first
sweeps due purges and releases any file the release lane left held - the
backstop for a kick that never arrived.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.inbound_attachment import InboundAttachment
from ..redis_client import get_redis, sync
from ..utils import file_crypto
from ..utils.dbresult import updated_rows
from ..utils.timeutil import utc_now, utc_now_aware
from . import av_release, file_encryption
from .audit import record_audit_event

logger = logging.getLogger("fileheron.encryption_lanes")

RELEASE_JOB = "encrypt_new_files"
_RELEASE_LOCK = "fh:enc:release:lock"
_RELEASE_DIRTY = "fh:enc:release:dirty"
# A killed worker frees the lock within this; a live holder renews it.
_LOCK_TTL_SEC = 600
_LOCK_RENEW_SEC = 60
# A mark that cannot be cleared (a Redis that answers GET but not GETDEL) must
# not spin the lane forever; what a capped run leaves, the next kick takes.
_MAX_PASSES = 50


class RedisLease:
    """A Redis lock that lives as long as its holder, renewed by a thread.

    Fails OPEN: with Redis down there is no lock, and the conditional UPDATEs
    still keep two runs from both swapping one row (the loser's copy is queued
    as debris)."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.token = secrets.token_hex(8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self) -> bool:
        try:
            if not get_redis().set(self.key, self.token, nx=True, ex=_LOCK_TTL_SEC):
                return False
        except Exception:
            logger.warning("lock %s unavailable (redis); running unguarded", self.key)
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._renew, name=f"lease:{self.key}", daemon=True)
        self._thread.start()
        return True

    def _renew(self) -> None:
        while not self._stop.wait(_LOCK_RENEW_SEC):
            try:
                r = get_redis()
                if r.get(self.key) != self.token:
                    return
                r.expire(self.key, _LOCK_TTL_SEC)
            except Exception:
                logger.warning("could not renew lock %s", self.key, exc_info=True)

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        try:
            r = get_redis()
            if r.get(self.key) == self.token:
                r.delete(self.key)
        except Exception:
            logger.warning("could not release lock %s; it expires on its own", self.key)


def _mark_dirty() -> None:
    try:
        get_redis().set(_RELEASE_DIRTY, "1", ex=_LOCK_TTL_SEC * 6)
    except Exception:
        logger.warning("could not mark the release lane dirty (redis)")


def _take_dirty() -> bool:
    try:
        return bool(get_redis().getdel(_RELEASE_DIRTY))
    except Exception:
        return False


def _is_dirty() -> bool:
    try:
        return bool(get_redis().get(_RELEASE_DIRTY))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The scan's side
# ---------------------------------------------------------------------------


def hold_for_encryption(db: Session, file: File, verdict: str) -> bool:
    """Record `verdict` for the lane instead of releasing the file. Conditional
    like the release itself: False when the row has left `ready_unscanned`
    (share expiry) or already carries a verdict. Never commits."""
    if verdict != av_release.VERDICT_CLEAN and not verdict.startswith("unscanned:"):
        raise ValueError(f"unknown verdict {verdict!r}")
    stmt = (
        update(File)
        .where(
            File.id == file.id,
            File.state == FileState.ready_unscanned,
            File.release_verdict.is_(None),
        )
        .values(release_verdict=verdict)
        .execution_options(synchronize_session=False)
    )
    return updated_rows(db.execute(stmt)) == 1


async def kick_release_lane() -> None:
    """Enqueue a lane run. Never raises: a verdict that is not picked up now is
    picked up by the scan sweep or the backfill."""
    from . import job_queue

    _mark_dirty()
    try:
        await job_queue.aenqueue(RELEASE_JOB)
    except Exception:
        logger.warning("could not enqueue %s; the sweep will", RELEASE_JOB, exc_info=True)


# ---------------------------------------------------------------------------
# The lane
# ---------------------------------------------------------------------------


def _reason(exc: BaseException) -> str:
    if isinstance(exc, file_encryption.InsufficientSpaceError):
        return "insufficient_space"
    if isinstance(exc, file_crypto.FileIntegrityError):
        return "integrity"
    return type(exc).__name__


def _release_without_encrypting(
    db: Session, file: File, verdict: str, *, reason: str | None
) -> str:
    share_id = file.share_id
    released = av_release.apply_verdict(
        db,
        file,
        verdict,
        extra_values={"release_verdict": None},
        extra_where=[File.release_verdict == verdict],
    )
    if not released:
        db.rollback()
        return "superseded"
    if reason is not None:
        record_audit_event(
            db,
            event_type=AuditEventType.file_encryption_deferred,
            actor_user_id=None,
            target_type="file",
            target_id=file.id,
            metadata={"reason": reason, "lane": "release"},
        )
    db.commit()
    av_release.notify_recipients_if_ready(db, share_id=share_id)
    return "plaintext" if reason is not None else "released"


def release_one(db: Session, file_id: str, *, cancel: threading.Event | None = None) -> str:
    """Encrypt one held file and release it. Returns what happened: encrypted,
    plaintext (encryption failed, released anyway), released (the switch went
    off meanwhile), superseded (the row moved underneath), or skipped."""
    db.expire_all()
    file = db.get(File, file_id)
    if file is None or file.state != FileState.ready_unscanned or not file.release_verdict:
        return "skipped"
    verdict = file.release_verdict
    if not file_encryption.is_enabled(db) or file.enc_version is not None or not file.storage_path:
        return _release_without_encrypting(db, file, verdict, reason=None)

    share_id = file.share_id
    target = file_encryption.target_for_file(file)
    try:
        prepared = file_encryption.prepare_rewrite(db, target, encrypt=True, cancel=cancel)
    except file_crypto.EncryptionCancelledError:
        raise
    except Exception as exc:
        db.rollback()
        logger.warning("release lane: could not encrypt %s; releasing it as plaintext", file_id,
                       exc_info=True)
        outcome = _release_without_encrypting(db, file, verdict, reason=_reason(exc))
        file_encryption.sweep_purges(db)
        return outcome

    swapped = av_release.apply_verdict(
        db,
        file,
        verdict,
        extra_values={**prepared.values, "release_verdict": None},
        extra_where=[
            File.storage_path == target.locator,
            File.enc_version.is_(None),
            File.release_verdict == verdict,
        ],
    )
    # Nobody could read the plaintext (the row answered 425 until this
    # commit), so it goes at once.
    file_encryption.finish_rewrite(db, prepared, swapped=swapped, purge_after=timedelta(0))
    db.commit()
    file_encryption.sweep_purges(db)
    if not swapped:
        return "superseded"
    av_release.notify_recipients_if_ready(db, share_id=share_id)
    return "encrypted"


def _held_ids(db: Session) -> list[str]:
    return [
        fid
        for (fid,) in db.query(File.id)
        .filter(File.state == FileState.ready_unscanned, File.release_verdict.isnot(None))
        .order_by(File.created_at, File.id)
        .all()
    ]


def run_release_lane(db: Session, *, cancel: threading.Event | None = None) -> dict[str, int]:
    """Release every held file, oldest first. Returns counts by outcome; `busy`
    when another run holds the lane (which will see this run's mark)."""
    totals: Counter[str] = Counter()
    _mark_dirty()
    for _pass in range(_MAX_PASSES):
        lease = RedisLease(_RELEASE_LOCK)
        if not lease.acquire():
            totals["busy"] += 1
            return dict(totals)
        try:
            # Clear the mark BEFORE reading the queue: a verdict committed
            # before this line is in the query, one marked after it is seen
            # by the check below.
            _take_dirty()
            for fid in _held_ids(db):
                if cancel is not None and cancel.is_set():
                    raise file_crypto.EncryptionCancelledError("release lane cancelled")
                totals[release_one(db, fid, cancel=cancel)] += 1
        finally:
            lease.release()
        # Checked AFTER letting go: a run that found the lane busy marked it
        # before trying, so either it got the lock itself or the mark is here.
        if not _is_dirty():
            return dict(totals)
    logger.warning("release lane: still marked after %d passes; leaving the rest to the next run",
                   _MAX_PASSES)
    totals["passes_exhausted"] += 1
    return dict(totals)


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------

BACKFILL_JOB = "encrypt_existing_files"
_BACKFILL_LOCK = "fh:enc:backfill:lock"
# Failures per stored object ("file:<id>" / "inbound_attachment:<id>"), and the
# objects deferred after `_FAIL_THRESHOLD` of them, scored by when the deferral
# ends. FIXED keys, read directly - never a keyspace walk.
_FAILURES_KEY = "fh:enc:backfill:failures"
_DEFERRED_KEY = "fh:enc:backfill:deferred"
_FAIL_THRESHOLD = 3
_DEFER_SEC = 24 * 3600
# Under the cron's 10-minute cadence plus the lock's own margin: a run never
# overlaps the next tick by much, and the lock refuses the overlap anyway.
BACKFILL_BUDGET_SEC = 50 * 60
_BATCH = 100
_BACKFILL_FILE_STATES = (FileState.clean, FileState.infected)


def _member(kind: str, row_id: str) -> str:
    return f"{kind}:{row_id}"


def _record_failure(member: str) -> bool:
    """Count a failed attempt; True when this one deferred the object. Fails
    open: with no Redis nothing is deferred and the next run tries again."""
    try:
        r = get_redis()
        if int(sync(r.hincrby(_FAILURES_KEY, member, 1))) < _FAIL_THRESHOLD:
            return False
        r.zadd(_DEFERRED_KEY, {member: utc_now_aware().timestamp() + _DEFER_SEC})
        r.hdel(_FAILURES_KEY, member)
        return True
    except Exception:
        logger.warning("backfill: could not record a failure (redis)")
        return False


def _clear_failure(member: str) -> None:
    try:
        get_redis().hdel(_FAILURES_KEY, member)
    except Exception:
        pass


def deferred_members() -> set[str] | None:
    """Objects currently deferred; None when Redis cannot say."""
    try:
        r = get_redis()
        r.zremrangebyscore(_DEFERRED_KEY, "-inf", f"({utc_now_aware().timestamp()}")
        return {m.decode() if isinstance(m, bytes) else m for m in sync(r.zrange(_DEFERRED_KEY, 0, -1))}
    except Exception:
        return None


def clear_deferrals() -> None:
    """The admin's "try the failed ones again". Raises when Redis is down, so
    the route can say so instead of claiming it worked."""
    get_redis().delete(_DEFERRED_KEY, _FAILURES_KEY)


def _candidates(db: Session, kind: str) -> Iterator[Any]:
    """The ids of every plaintext object of `kind`, in id order, a batch at a
    time (keyset paging: a failed object is never re-selected inside one run)."""
    last: Any = None
    while True:
        ids: list[Any]
        if kind == file_encryption.KIND_FILE:
            fq = db.query(File.id).filter(
                File.enc_version.is_(None),
                File.state.in_(_BACKFILL_FILE_STATES),
                File.storage_path.isnot(None),
            )
            if last is not None:
                fq = fq.filter(File.id > last)
            ids = [i for (i,) in fq.order_by(File.id).limit(_BATCH).all()]
        else:
            aq = db.query(InboundAttachment.id).filter(
                InboundAttachment.enc_version.is_(None),
                InboundAttachment.storage_key.isnot(None),
            )
            if last is not None:
                aq = aq.filter(InboundAttachment.id > last)
            ids = [i for (i,) in aq.order_by(InboundAttachment.id).limit(_BATCH).all()]
        if not ids:
            return
        last = ids[-1]
        yield from ids


def _plaintext_remaining(db: Session) -> int:
    from sqlalchemy import func

    files = db.query(func.count(File.id)).filter(
        File.enc_version.is_(None), File.state.in_(_BACKFILL_FILE_STATES), File.storage_path.isnot(None)
    ).scalar() or 0
    atts = db.query(func.count(InboundAttachment.id)).filter(
        InboundAttachment.enc_version.is_(None), InboundAttachment.storage_key.isnot(None)
    ).scalar() or 0
    return int(files) + int(atts)


def _target(db: Session, kind: str, row_id: Any) -> file_encryption.Target | None:
    if kind == file_encryption.KIND_FILE:
        f = db.get(File, row_id)
        if f is None or f.enc_version is not None or f.state not in _BACKFILL_FILE_STATES or not f.storage_path:
            return None
        return file_encryption.target_for_file(f)
    a = db.get(InboundAttachment, row_id)
    if a is None or a.enc_version is not None or not a.storage_key:
        return None
    return file_encryption.target_for_attachment(a)


def run_backfill(
    db: Session,
    *,
    cancel: threading.Event | None = None,
    budget_sec: float = BACKFILL_BUDGET_SEC,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    from . import settings as settings_svc
    from . import transfer_activity
    from .cron_tracker import CRON_FAILED_KEY

    purged = file_encryption.sweep_purges(db)
    held = run_release_lane(db, cancel=cancel)
    if not file_encryption.is_enabled(db):
        return {"skipped": "disabled", "purged": purged["purged"], "held_released": held}
    lease = RedisLease(_BACKFILL_LOCK)
    if not lease.acquire():
        return {"skipped": "busy", "purged": purged["purged"]}

    counts: Counter[str] = Counter()
    stopped: str | None = None
    deadline = clock() + budget_sec
    deferred = deferred_members() or set()
    try:
        for kind in (file_encryption.KIND_FILE, file_encryption.KIND_ATTACHMENT):
            for row_id in _candidates(db, kind):
                if cancel is not None and cancel.is_set():
                    raise file_crypto.EncryptionCancelledError("backfill cancelled")
                if clock() >= deadline:
                    stopped = "budget"
                    break
                member = _member(kind, str(row_id))
                if member in deferred:
                    counts["deferred_skipped"] += 1
                    continue
                if kind == file_encryption.KIND_FILE and transfer_activity.was_download_recent(str(row_id)):
                    counts["recently_served"] += 1
                    continue
                target = _target(db, kind, row_id)
                if target is None:
                    continue
                try:
                    swapped = file_encryption.rewrite_stored(
                        db,
                        target,
                        encrypt=True,
                        purge_after=file_encryption.PLAINTEXT_PURGE_GRACE,
                        expected_states=_BACKFILL_FILE_STATES if kind == file_encryption.KIND_FILE else None,
                        cancel=cancel,
                    )
                except file_crypto.EncryptionCancelledError:
                    raise
                except file_encryption.InsufficientSpaceError:
                    db.rollback()
                    stopped = "insufficient_space"
                    break
                except Exception as exc:
                    db.rollback()
                    counts["failed"] += 1
                    logger.warning("backfill: could not encrypt %s", member, exc_info=True)
                    if _record_failure(member):
                        counts["deferred"] += 1
                        record_audit_event(
                            db,
                            event_type=AuditEventType.file_encryption_failed,
                            actor_user_id=None,
                            target_type=kind,
                            target_id=str(row_id),
                            metadata={"reason": _reason(exc), "attempts": _FAIL_THRESHOLD},
                        )
                        db.commit()
                    continue
                if swapped:
                    counts["encrypted"] += 1
                    _clear_failure(member)
                else:
                    counts["superseded"] += 1
            if stopped is not None:
                break
    finally:
        lease.release()

    summary: dict[str, Any] = {
        "finished_at": utc_now().replace(microsecond=0).isoformat(),
        "encrypted": counts["encrypted"],
        "failed": counts["failed"],
        "deferred": counts["deferred"],
        "skipped": counts["deferred_skipped"] + counts["recently_served"],
        "remaining": _plaintext_remaining(db),
        "stopped": stopped,
    }
    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_LAST_RUN,
                           value=json.dumps(summary), actor=None)
    db.commit()
    result: dict[str, Any] = {**summary, "purged": purged["purged"], "held_released": held}
    # A run that tried and got nothing done is a failed run: the Scheduled
    # tasks page and the cron alert must see it, not a green "success".
    if (counts["failed"] and not counts["encrypted"] and not counts["superseded"]) or (
        stopped == "insufficient_space" and not counts["encrypted"]
    ):
        result[CRON_FAILED_KEY] = True
    return result
