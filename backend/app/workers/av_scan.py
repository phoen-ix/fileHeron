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

from ..config import settings
from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..services import av_scan as av_scan_svc
from ..services.audit import record_audit_event
from ..services.quarantine import quarantine_file

logger = logging.getLogger("fileheron.workers.av_scan")


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

        # Local backend → path-scan (clamd reads the shared mount). Object store →
        # stream the bytes to clamd via INSTREAM (no shared path).
        from ..services.storage_backend import get_storage_backend
        backend = get_storage_backend()
        local = backend.local_path(file.storage_path)
        # Both scan paths are BLOCKING socket I/O, and this is an `async def`
        # running on the ARQ worker's single event loop - so a slow scan used to
        # freeze every other job in the process (send_email, webhook_deliver,
        # every cron) for its whole duration, up to the socket timeout per file.
        # Hand them to a thread so only this task waits (audit 2026-07-30).
        def _scan() -> av_scan_svc.ScanResult:
            if local is not None:
                return av_scan_svc.scan_path(local)
            with backend.open(file.storage_path) as fh:
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
            raise Retry(defer=min(60, 5 * attempt)) from e

        if result.state == "clean":
            # clamd answers "clean" for a file past its size limit without ever
            # reading it - it just stops scanning. So a "clean" verdict above
            # AV_MAX_SCAN_BYTES is not evidence of anything.
            #
            # That limit is not the operator's to raise: clamd clamps
            # MaxFileSize to INT_MAX (~2 GiB) whatever clamd.conf says, so no
            # configuration makes it scan a 5 GB upload. fileHeron deliberately
            # supports uploads far larger than that, so the file IS still
            # served - but it is recorded as unscanned rather than clean, and
            # the API, UI and audit trail say so (audit 2026-07-30).
            oversize = (
                (file.size_bytes or 0) > settings.AV_MAX_SCAN_BYTES
                and not settings.AV_SKIP
            )
            if oversize:
                logger.warning(
                    "av_scan: %s is %d bytes, over AV_MAX_SCAN_BYTES (%d); clamd "
                    "cannot scan it - serving as UNSCANNED, not clean",
                    file_id,
                    file.size_bytes or 0,
                    settings.AV_MAX_SCAN_BYTES,
                )
            # Conditional flip: a slow scan can run while share expiry commits
            # `deleted` (bytes gone). Only mark clean if the row is still
            # ready_unscanned, else we would resurrect a deleted file whose
            # bytes no longer exist (mirrors approve_share/expire_share_now).
            updated = (
                db.query(File)
                .filter(File.id == file_id, File.state == FileState.ready_unscanned)
                .update(
                    {File.state: FileState.clean, File.av_unscanned: oversize},
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
            if oversize:
                # Durable record that a file was released without a real
                # verdict. Written in the same transaction as the state flip.
                record_audit_event(
                    db,
                    event_type=AuditEventType.file_served_unscanned,
                    actor_user_id=file.uploaded_by_id,
                    target_type="file",
                    target_id=file_id,
                    metadata={
                        "size_bytes": file.size_bytes,
                        "av_max_scan_bytes": settings.AV_MAX_SCAN_BYTES,
                        "reason": "exceeds_clamd_max_file_size",
                    },
                )
            db.commit()
            if oversize:
                return {
                    "file_id": file_id,
                    "state": "clean",
                    "av_unscanned": True,
                    "size_bytes": file.size_bytes,
                }
            logger.info("av_scan: %s clean", file_id)
            return {"file_id": file_id, "state": "clean"}

        if result.state == "infected":
            # Same guard as the clean path: if the row left ready_unscanned
            # mid-scan (share expiry committed `deleted` and freed the bytes),
            # don't resurrect it into `infected` - which would also revoke a
            # dead share and fire an infection notice for a file that's gone.
            current_state = db.query(File.state).filter(File.id == file_id).scalar()
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
        # Don't quarantine; leave in ready_unscanned for a manual rescan
        # or a retry on next worker cycle.
        logger.error("av_scan: %s error from clamd: %s", file_id, result.raw)
        return {"file_id": file_id, "state": "error", "raw": result.raw}
    finally:
        db.close()
