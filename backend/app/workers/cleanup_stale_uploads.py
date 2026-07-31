"""Hourly job: reap uploads abandoned mid-stream.

A `files` row is created in `state=uploading` at upload init, then flips to
`ready_unscanned` once the bytes land (TUS post-finish hook or the direct
endpoint). If the client never finishes - tab closed, network drop, a failed
or cancelled transfer - the row sits in `uploading` forever, and because the
sent folder lists by *share* state the parent share shows as a perpetual
upload.

`cleanup_abandoned_uploads` only sweeps tusd's disk working dir and deliberately
skips uploads whose row is still `uploading`, so nothing here overlaps. This
job is the DB-side reaper: it finds `files` rows stuck in `uploading` past
`retention.upload_stale_hours`, deletes any partial bytes, marks the file
`deleted`, and - when the share has no usable file left - flips the share to
`failed` so it drops out of the active sent folder.

Quota: we intentionally do NOT release_bytes here. Some abandoned rows never
reserved any (no tus_upload_id / no bytes on disk), so releasing would
over-credit; the hourly `quota_reconcile` recomputes used-bytes from disk and
is the single source of truth for drift.

Idempotent: a second run finds nothing (rows are `deleted`, shares out of
`active`). Per-file failures don't abort the batch.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from ..config import settings
from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..services import job_queue
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.cleanup_stale_uploads")

# A share stays active while any file is still in flight or usable.
_USABLE_FILE_STATES = {
    FileState.uploading,
    FileState.ready_unscanned,
    FileState.clean,
}

# Files finalized this long ago that are still ready_unscanned had their scan
# fail or never run (transient clamd error, a missed enqueue - audit L7, or a
# worker crash); re-enqueue a scan so they don't stay un-downloadable forever
# (audit L8). The normal post-finish scan completes in seconds, so this window
# never races an in-flight scan.
_RESCAN_STUCK_AFTER_MIN = 30
_RESCAN_BATCH = 500




def _unlink_partial_bytes(file: File) -> None:
    """Best-effort removal of any remnants of an abandoned upload."""
    # Finalized bytes (if any) live in the storage backend.
    if file.storage_path:
        from ..services.storage_backend import get_storage_backend
        try:
            get_storage_backend().delete(file.storage_path)
        except Exception as e:
            logger.warning(
                "cleanup_stale_uploads: storage delete failed file=%s: %s", file.id, e
            )
    # The tusd working files always stage on the local disk regardless of backend.
    if file.tus_upload_id:
        base = Path(settings.TUS_UPLOAD_DIR)
        for p in (base / file.tus_upload_id, base / f"{file.tus_upload_id}.info"):
            try:
                if p.is_file():
                    p.unlink()
            except OSError as e:
                logger.warning(
                    "cleanup_stale_uploads: unlink failed file=%s path=%s: %s",
                    file.id, p, e,
                )


@track_cron("cleanup_stale_uploads")
async def cleanup_stale_uploads(_ctx) -> dict:
    db = SessionLocal()
    files_reaped = 0
    shares_failed = 0
    scanned = 0
    try:
        from ..services import settings_registry

        hours = settings_registry.effective(
            db, settings_registry.K.UPLOAD_STALE_AFTER_HOURS
        )
        cutoff = utc_now() - timedelta(hours=hours)

        stale = (
            db.query(File)
            .filter(File.state == FileState.uploading, File.created_at < cutoff)
            .all()
        )
        scanned = len(stale)

        # Group by share so we evaluate each share's "any usable file left?"
        # once, after reaping its stale files.
        share_ids = {f.share_id for f in stale}

        for f in stale:
            _unlink_partial_bytes(f)
            f.state = FileState.deleted
            record_audit_event(
                db,
                event_type=AuditEventType.file_upload_abandoned,
                actor_user_id=None,
                target_type="file",
                target_id=f.id,
                metadata={
                    "size_bytes": f.size_bytes,
                    "filename": f.original_filename,
                    "share_id": f.share_id,
                },
            )
            files_reaped += 1

        db.flush()

        now = utc_now()
        for share_id in share_ids:
            share = db.query(Share).filter(Share.id == share_id).first()
            if share is None or share.state != ShareState.active:
                continue
            has_usable = any(fl.state in _USABLE_FILE_STATES for fl in share.files)
            if has_usable:
                continue
            share.state = ShareState.failed
            share.terminated_at = now
            record_audit_event(
                db,
                event_type=AuditEventType.share_failed,
                actor_user_id=None,
                target_type="share",
                target_id=share.id,
                metadata={"reason": "upload_abandoned"},
            )
            shares_failed += 1

        db.commit()

        # Recover scans that never completed: re-enqueue ready_unscanned files
        # stuck past _RESCAN_STUCK_AFTER_MIN.
        #
        # This used to exclude `size_bytes > AV_MAX_SCAN_BYTES`, on the grounds
        # that clamd cannot scan them so "re-scanning would loop forever". The
        # premise was an object-store failure mode generalised to both backends,
        # and the exclusion turned a recoverable state into a permanent one:
        # a file over the limit whose scan job burned its retries (any clamav
        # restart, reboot or OOM would do it) stayed at `ready_unscanned` with
        # nothing left to move it, and every download of it - browser, desktop
        # client or public link - answered `425 SCAN_IN_PROGRESS`, "try again
        # shortly", forever. Its bytes kept counting against quota. Recovery
        # needed hand-written SQL. Files UNDER the limit self-healed here within
        # 30 minutes, so every test and dev upload exercised the working path
        # and the failure was reserved for the flagship 30 GB workload
        # (audit 2026-07-30).
        #
        # `av_scan_file` now decides unscannable BEFORE scanning and releases
        # those files as `clean` + `av_unscanned` in one pass, on either
        # backend, so re-enqueueing THEM terminates. Do not reintroduce a size
        # filter here: this sweep is the only automated recovery there is.
        #
        # It is not a universal termination proof, and should not be read as
        # one. A scan that keeps failing the same way - clamd returning `error`,
        # or a job that exceeds WorkerSettings.job_timeout - still comes back
        # here next cycle. That is deliberate (the alternative is abandoning a
        # file with no verdict and no recovery), but it means a persistently
        # failing scan shows up as repeated work and NOTHING ELSE.
        #
        # Nothing surfaces it today, and saying "ops_check should" would be the
        # same kind of comment this release exists to delete: `notify_admin_error`
        # is fed by the HTTP error middleware, the telemetry routes, and
        # cron_tracker when a cron RAISES. `av_scan_file` returning
        # `{"state": "error"}` is none of those. Wiring it up is worth doing;
        # until then this is a known blind spot, written down as one.
        rescan_cutoff = utc_now() - timedelta(minutes=_RESCAN_STUCK_AFTER_MIN)
        stuck = (
            db.query(File)
            .filter(
                File.state == FileState.ready_unscanned,
                File.finalized_at.isnot(None),
                File.finalized_at < rescan_cutoff,
            )
            # Ordered: a bare LIMIT over an unordered query lets the database
            # return a different 500 each cycle, so with more stuck files than
            # _RESCAN_BATCH some could be passed over indefinitely. Oldest
            # first, which is also the order an operator would expect.
            .order_by(File.finalized_at.asc(), File.id.asc())
            .limit(_RESCAN_BATCH)
            .all()
        )
        # One Redis pool for the batch, not one per file: `aenqueue` opens and
        # closes a connection pool per call, and this loop can be 500 long
        # (job_queue's own docstring says to use the batch form wherever the
        # count scales with anything but a constant).
        await job_queue.aenqueue_many(
            [("av_scan_file", (f.id,), {}) for f in stuck]
        )
        rescans_requeued = len(stuck)

        if files_reaped or shares_failed or rescans_requeued:
            logger.info(
                "cleanup_stale_uploads: scanned=%d files_reaped=%d shares_failed=%d "
                "rescans_requeued=%d",
                scanned, files_reaped, shares_failed, rescans_requeued,
            )
        return {
            "scanned": scanned,
            "files_reaped": files_reaped,
            "shares_failed": shares_failed,
            "rescans_requeued": rescans_requeued,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
