"""ARQ worker task — scan an uploaded file via clamd.

Triggered by the tusd post-finish hook (synchronous → enqueues here).
Reads the file path from the DB row, asks clamd, and either:
- marks state=clean, or
- moves the file into quarantine + revokes the parent share.

Retries (configured at the WorkerSettings level) handle the transient
"clamd not yet ready / network blip" case.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..models.file import File, FileState
from ..services import av_scan as av_scan_svc
from ..services.quarantine import quarantine_file

logger = logging.getLogger("fileheron.workers.av_scan")


async def av_scan_file(_ctx, file_id: str) -> dict:
    """Scan a single file. Idempotent — silently skips files not in
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

        # Local backend → path-scan (clamd reads the shared mount). PR-B adds the
        # INSTREAM branch for object stores (local_path() is None there).
        from ..services.storage_backend import get_storage_backend
        scan_target = get_storage_backend().local_path(file.storage_path)
        try:
            result = av_scan_svc.scan_path(scan_target)
        except av_scan_svc.AVUnavailableError as e:
            # Re-raise so ARQ retries with backoff. Worker config picks
            # the retry count.
            logger.warning("clamd unavailable for %s: %s", file_id, e)
            raise

        if result.state == "clean":
            file.state = FileState.clean
            db.commit()
            logger.info("av_scan: %s clean", file_id)
            return {"file_id": file_id, "state": "clean"}

        if result.state == "infected":
            quarantine_file(db, file=file, signature=result.signature)
            db.commit()
            logger.warning(
                "av_scan: %s INFECTED (%s) — quarantined", file_id, result.signature
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
