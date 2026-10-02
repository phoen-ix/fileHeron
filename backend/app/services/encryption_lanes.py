"""Encryption at rest - the lane that encrypts a new upload before release.

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
"""
from __future__ import annotations

import logging
import secrets
import threading
from collections import Counter
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..redis_client import get_redis
from ..utils import file_crypto
from ..utils.dbresult import updated_rows
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
