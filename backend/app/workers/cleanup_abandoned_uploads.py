"""Hourly cron: clean up TUS uploads abandoned mid-stream.

`/data/uploads/` is tusd's working directory. Each in-flight upload is
a binary file with the tusd id as filename plus a sidecar `<id>.info`
JSON. The post-terminate hook releases quota when a client explicitly
aborts; a browser tab closed mid-upload, network drop, or backend
restart between pre-create and pre-finish leaves the partial bytes
on disk indefinitely.

This cron walks `/data/uploads/`, finds `<id>.info` sidecars older
than `TUS_UPLOAD_ABANDONED_AFTER_HOURS`, cross-checks the DB to
confirm no live `files` row references the upload id, and unlinks
both the data file and the sidecar.

Idempotent: a second run finds nothing if the first cleaned up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from ..database import SessionLocal
from ..models.file import File, FileState
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.cleanup_abandoned_uploads")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@track_cron("cleanup_abandoned_uploads")
async def cleanup_abandoned_uploads(_ctx) -> dict:
    upload_dir = Path(settings.TUS_UPLOAD_DIR)
    if not upload_dir.is_dir():
        return {"scanned": 0, "deleted": 0, "skipped_active": 0}

    cutoff = _utcnow() - timedelta(hours=settings.TUS_UPLOAD_ABANDONED_AFTER_HOURS)
    scanned = 0
    deleted = 0
    skipped_active = 0

    db = SessionLocal()
    try:
        for info_path in upload_dir.glob("*.info"):
            scanned += 1
            try:
                mtime = datetime.fromtimestamp(info_path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
            except OSError:
                continue
            if mtime > cutoff:
                continue

            tus_id = info_path.stem  # filename without `.info`
            # If the DB still has a live `files` row pointing at this
            # tus_id, leave it alone — the finalize hook may yet land.
            row = (
                db.query(File)
                .filter(
                    File.tus_upload_id == tus_id,
                    File.state == FileState.uploading,
                )
                .first()
            )
            if row is not None:
                skipped_active += 1
                continue

            data_path = upload_dir / tus_id
            try:
                if data_path.is_file():
                    data_path.unlink()
                info_path.unlink()
                deleted += 1
            except OSError as e:
                logger.warning(
                    "cleanup_abandoned_uploads: unlink failed tus_id=%s: %s",
                    tus_id, e,
                )

        if deleted or skipped_active:
            logger.info(
                "cleanup_abandoned_uploads: scanned=%d deleted=%d skipped_active=%d",
                scanned, deleted, skipped_active,
            )
        return {
            "scanned": scanned,
            "deleted": deleted,
            "skipped_active": skipped_active,
        }
    finally:
        db.close()
