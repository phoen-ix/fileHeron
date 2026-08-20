"""ARQ worker task - scan an uploaded file via clamd.

Triggered by the tusd post-finish hook (synchronous → enqueues here).
Reads the file path from the DB row, asks clamd, and either:
- marks state=clean, or
- moves the file into quarantine + revokes the parent share.

Retries (configured at the WorkerSettings level) handle the transient
"clamd not yet ready / network blip" case.
"""
from __future__ import annotations

import asyncio
import logging

from arq import Retry

from ..config import CLAMD_MAX_FILE_SIZE, settings
from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..services import av_scan as av_scan_svc
from ..services.audit import record_audit_event
from ..services.quarantine import quarantine_file

logger = logging.getLogger("fileheron.workers.av_scan")

# Ceiling on the per-attempt backoff when clamd is unavailable. With
# WorkerSettings.max_tries = 5 the four retries span 30+60+90+120 = 300s, which
# has to cover a clamav COLD START - docker-compose budgets its healthcheck 180s
# and freshclam's first mirror sync is far longer.
_RETRY_MAX_DEFER_SEC = 300


def _release_unscanned(db, *, file_id: str, file: File, reason: str) -> dict:
    """Release a file without a trusted verdict: `clean` state,
    `av_unscanned = True`, and a durable audit row saying which threshold did it.

    Two distinct reasons reach here, and keeping them distinct is the point:

    - `exceeds_clamd_max_file_size` - past `CLAMD_MAX_FILE_SIZE`, the ceiling
      clamd clamps itself to whatever clamd.conf says. No verdict is obtainable,
      so the scan is skipped entirely.
    - `exceeds_av_max_scan_bytes` - clamd WAS asked and answered clean, but the
      operator's trust threshold says not to record that as a verdict. The scan
      still happened, so an infected reply would have been quarantined.

    fileHeron deliberately accepts uploads far larger than anything clamd will
    read, so the choice is between serving the file with an honest label and
    never serving it at all. It is served, and the API, the UI badge and the
    audit trail all say it was not scanned.

    Terminal matters as much as honest: the previous behaviour on the object
    store left these files at `ready_unscanned` forever, where every download
    answered `425 SCAN_IN_PROGRESS` - "try again shortly" - about a scan that
    was never going to happen."""
    # Conditional flip, same reason as the clean path: share expiry may have
    # committed `deleted` and freed the bytes while this ran.
    updated = (
        db.query(File)
        .filter(File.id == file_id, File.state == FileState.ready_unscanned)
        .update(
            {File.state: FileState.clean, File.av_unscanned: True},
            synchronize_session=False,
        )
    )
    if updated == 0:
        db.rollback()
        logger.info(
            "av_scan: %s left ready_unscanned mid-scan; not releasing", file_id
        )
        return {"file_id": file_id, "state": "superseded"}
    # Written in the same transaction as the state flip.
    record_audit_event(
        db,
        event_type=AuditEventType.file_served_unscanned,
        actor_user_id=file.uploaded_by_id,
        target_type="file",
        target_id=file_id,
        metadata={
            "size_bytes": file.size_bytes,
            "av_max_scan_bytes": settings.AV_MAX_SCAN_BYTES,
            "reason": reason,
        },
    )
    db.commit()
    logger.warning(
        "av_scan: %s (%d bytes) released as UNSCANNED, not clean - %s",
        file_id,
        file.size_bytes or 0,
        reason,
    )
    return {
        "file_id": file_id,
        "state": "clean",
        "av_unscanned": True,
        "size_bytes": file.size_bytes,
    }


async def av_scan_file(_ctx, file_id: str) -> dict:
    """Scan a single file. Idempotent - silently skips files not in
    `ready_unscanned` state (already scanned, deleted, etc.)."""
    db = SessionLocal()
    try:
        file = db.query(File).filter(File.id == file_id).one_or_none()
        if file is None:
            logger.warning("av_scan: unknown file %s", file_id)
            return {"file_id": file_id, "state": "missing"}
        if file.state != FileState.ready_unscanned:
            logger.info(
                "av_scan: file %s in state %s; skipping",
                file_id,
                file.state.value,
            )
            return {"file_id": file_id, "state": file.state.value, "skipped": True}
        if not file.storage_path:
            logger.warning("av_scan: file %s has no storage_path", file_id)
            return {"file_id": file_id, "state": "no_path"}
        # Bound here, not read inside _scan(): narrowing does not survive into a
        # nested function, because the attribute could be rebound before the call.
        locator = file.storage_path

        # Local backend → path-scan (clamd reads the shared mount). Object store →
        # stream the bytes to clamd via INSTREAM (no shared path).
        from ..services.storage_backend import get_storage_backend
        backend = get_storage_backend()
        local = backend.local_path(locator)

        # Decide unscannable BEFORE scanning, against CLAMD_MAX_FILE_SIZE - the
        # ceiling clamd clamps ITSELF to, not the operator-tunable
        # AV_MAX_SCAN_BYTES. Keying this skip off the tunable would turn a
        # documented knob into a silent antivirus off-switch: lowering it (a
        # small clamd, a slow disk - docker/clamav/clamd.conf invites exactly
        # that) would stop files above the new value being scanned at all, and
        # an infected one would be released `clean` instead of quarantined.
        # The tunable's job is further down, and it is only about TRUST.
        #
        # clamd cannot produce a verdict for a file past its MaxFileSize, and the two backends failed differently on
        # it: a path-scan answers a meaningless "OK" (handled below), while
        # INSTREAM answers `error` - which is not a terminal state, so the file
        # sat at ready_unscanned and got re-enqueued forever. Every download of
        # it, browser or client or public link, returned 425 "try again shortly"
        # about something that was never going to succeed (audit 2026-07-30).
        #
        # Not scanning at all is the honest answer, and it terminates: there is
        # no verdict to be had, so record the file as served-unscanned in one
        # pass. It also stops streaming multi-gigabyte objects out of S3 to be
        # rejected on arrival.
        #
        # This branch skips the AV scan, so it is only safe because size_bytes
        # cannot be inflated to reach it: the tus pre-finish hook refuses the
        # upload unless the final size equals the HMAC-authorised max_size
        # (tus_hooks._pre_finish), and that authorised size is what the row
        # carries; the direct-upload route records the bytes it actually
        # received. Getting here therefore costs a real multi-gigabyte transfer
        # - which is exactly the case where clamd was never going to produce a
        # verdict anyway. Do not relax either check without revisiting this.
        if (file.size_bytes or 0) > CLAMD_MAX_FILE_SIZE and not settings.AV_SKIP:
            return _release_unscanned(
                db, file_id=file_id, file=file, reason="exceeds_clamd_max_file_size"
            )
        # Both scan paths are BLOCKING socket I/O, and this is an `async def`
        # running on the ARQ worker's single event loop - so a slow scan used to
        # freeze every other job in the process (send_email, webhook_deliver,
        # every cron) for its whole duration, up to the socket timeout per file.
        # Hand them to a thread so only this task waits (audit 2026-07-30).
        def _scan() -> av_scan_svc.ScanResult:
            if local is not None:
                return av_scan_svc.scan_path(local)
            with backend.open(locator) as fh:
                return av_scan_svc.scan_stream(fh)

        try:
            result = await asyncio.to_thread(_scan)
        except av_scan_svc.AVUnavailableError as e:
            # clamd is down/not-ready. A plain re-raise is NOT re-enqueued by
            # arq (only Retry/RetryJob are), so it would burn the job with no
            # retry; raise Retry so max_tries applies with a capped backoff.
            # cleanup_stale_uploads recovers anything that outlives the retries.
            attempt = _ctx.get("job_try", 1)
            logger.warning("clamd unavailable for %s (try %d): %s", file_id, attempt, e)
            # The backoff has to outlast a clamav COLD START, not just a blip.
            # `min(60, 5 * attempt)` gave 5+10+15+20 = 50 seconds across the
            # four retries `max_tries=5` allows, while docker-compose budgets
            # clamav 180s to become healthy (freshclam's first mirror sync is
            # far longer). So any clamav restart, host reboot or OOM burned
            # every in-flight scan job. 30+60+90+120 = 300s covers it, and
            # cleanup_stale_uploads is still the backstop for anything that
            # outlives even that (audit 2026-07-30).
            raise Retry(defer=min(_RETRY_MAX_DEFER_SEC, 30 * attempt)) from e

        if result.state == "clean":
            # AV_MAX_SCAN_BYTES is the TRUST threshold, and it is a different
            # thing from the skip above. clamd was asked and answered; the
            # question here is whether that answer is worth recording as a
            # verdict. An operator may legitimately lower this (a small clamd,
            # a slow disk) - and lowering it must mean "stop believing clean
            # above this size", never "stop scanning above this size". Making
            # it a skip threshold would turn a documented tuning knob into a
            # silent antivirus off-switch: an infected file above the value
            # would be released `clean` instead of quarantined.
            if (
                (file.size_bytes or 0) > settings.AV_MAX_SCAN_BYTES
                and not settings.AV_SKIP
            ):
                return _release_unscanned(
                    db,
                    file_id=file_id,
                    file=file,
                    reason="exceeds_av_max_scan_bytes",
                )
            # Conditional flip: a slow scan can run while share expiry commits
            # `deleted` (bytes gone). Only mark clean if the row is still
            # ready_unscanned, else we would resurrect a deleted file whose
            # bytes no longer exist (mirrors approve_share/expire_share_now).
            updated = (
                db.query(File)
                .filter(File.id == file_id, File.state == FileState.ready_unscanned)
                .update(
                    {File.state: FileState.clean, File.av_unscanned: False},
                    synchronize_session=False,
                )
            )
            if updated == 0:
                db.rollback()
                logger.info(
                    "av_scan: %s left ready_unscanned mid-scan; not marking clean",
                    file_id,
                )
                return {"file_id": file_id, "state": "superseded"}
            db.commit()
            logger.info("av_scan: %s clean", file_id)
            return {"file_id": file_id, "state": "clean"}

        if result.state == "infected":
            # Same guard as the clean path: if the row left ready_unscanned
            # mid-scan (share expiry committed `deleted` and freed the bytes),
            # don't resurrect it into `infected` - which would also revoke a
            # dead share and fire an infection notice for a file that's gone.
            # A LOCKING read. Under MariaDB's REPEATABLE READ a plain SELECT
            # answers from this transaction's snapshot, so a `deleted` the API
            # connection committed while clamd was working is invisible - the
            # guard could not fire at all, and quarantine_file then flipped a
            # deleted row back to `infected`, revoked a share that still had
            # other clean files in it, released the same bytes from the quota a
            # second time and emailed the uploader about a file they had already
            # deleted (audit #2).
            current_state = (
                db.query(File.state)
                .filter(File.id == file_id)
                .with_for_update()
                .scalar()
            )
            if current_state != FileState.ready_unscanned:
                db.rollback()
                logger.info(
                    "av_scan: %s left ready_unscanned mid-scan; not quarantining", file_id
                )
                return {"file_id": file_id, "state": "superseded"}
            quarantine_file(db, file=file, signature=result.signature)
            db.commit()
            logger.warning(
                "av_scan: %s INFECTED (%s) - quarantined", file_id, result.signature
            )
            return {
                "file_id": file_id,
                "state": "infected",
                "signature": result.signature,
            }

        # ScanResult.state == "error": clamd answered but couldn't decide.
        # Don't quarantine; leave in ready_unscanned. `cleanup_stale_uploads`
        # re-enqueues it once it is 30 minutes past finalize, and the cron runs
        # hourly - so recovery is 30-90 minutes away, not immediate.
        #
        # There is NO manual rescan. This comment used to name one, as did
        # tus_hooks; no rescan endpoint or admin action exists anywhere in the
        # product. If clamd keeps answering `error` for the same file, this is
        # the path that loops: the sweep re-enqueues, the scan fails the same
        # way, and nothing escalates. See the note in cleanup_stale_uploads.
        logger.error("av_scan: %s error from clamd: %s", file_id, result.raw)
        return {"file_id": file_id, "state": "error", "raw": result.raw}
    finally:
        db.close()
