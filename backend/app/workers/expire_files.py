"""Hourly job: expire shares whose expires_at has passed.

For each expired share that's still `active`:
- transition state → `expired`
- hard-delete every file on disk
- emit audit_log(file_expired) per file + audit_log(share_expired) per share

Idempotent: re-running the job picks up no work the second time because
shares move out of `active` after the first run. File deletion is also
idempotent (silently no-ops on missing files).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import selectinload

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.file import FileState
from ..models.share import Share, ShareState
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..services.quota import release_bytes
from ..services.storage_backend import get_storage_backend
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.expire_files")




def _record_orphan_locator(db, *, locator: str, reason: str) -> None:
    """Leave a durable trace of bytes that failed to unlink.

    The old comment promised these were "cleaned by orphan-reclaim / disk
    sweep", but reclaim_orphaned_files works from DB rows and the row has
    already been flipped to `deleted` by the time the purge runs - so nothing
    could ever see the locator again and the bytes leaked silently, charged to
    nobody and visible to no one. An audit row is the cheapest durable record
    that needs no schema change, and it puts the locator somewhere an operator
    actually looks (audit 2026-07-30)."""
    try:
        record_audit_event(
            db,
            event_type=AuditEventType.file_purge_failed,
            actor_user_id=None,
            target_type="file_bytes",
            target_id=locator[:255],
            metadata={"locator": locator, "reason": reason},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("expire_files: could not record orphan locator %s", locator)


@track_cron("expire_files")
async def expire_files(_ctx) -> dict:
    """Walk shares.expires_at < now, transition state + hard-delete files.

    Per-share commit so a single bad share (e.g., disk unlink failure on
    one file) doesn't poison the rest of the batch."""
    db = SessionLocal()
    expired_shares = 0
    deleted_files = 0
    failed_shares = 0
    try:
        now = utc_now()
        shares = (
            db.query(Share)
            .options(selectinload(Share.files))
            .filter(Share.state == ShareState.active, Share.expires_at < now)
            .all()
        )
        for share in shares:
            # (locator, user_id, size) to purge AFTER the commit succeeds.
            # (locator, user_id, size, was_infected)
            to_purge: list[tuple[str | None, int, int, bool]] = []
            file_count = 0
            for f in share.files:
                if f.state == FileState.deleted:
                    continue
                # An `infected` file already had its bytes MOVED to quarantine
                # and its quota released by services/quarantine.py. This loop
                # was inlined rather than calling delete_file_for_expiry, so it
                # never picked up that helper's `was_infected` guard: it
                # unlinked the QUARANTINE locator (destroying evidence an admin
                # can otherwise release or inspect) and released the same bytes
                # a second time, silently inflating the uploader's free quota
                # (audit 2026-07-30).
                was_infected = f.state == FileState.infected
                # Mark deleted + audit now, but DON'T unlink bytes or release
                # quota yet. The irreversible byte delete + non-transactional
                # Redis release happen only AFTER the per-share commit (below),
                # so a commit failure can't leave a still-'clean' row whose
                # bytes are already gone (silent data loss) nor double-release
                # the quota on the next cron cycle (audit M14).
                to_purge.append(
                    (
                        None if was_infected else f.storage_path,
                        f.uploaded_by_id,
                        f.size_bytes,
                        was_infected,
                    )
                )
                f.state = FileState.deleted
                record_audit_event(
                    db,
                    event_type=AuditEventType.file_expired,
                    actor_user_id=None,
                    target_type="file",
                    target_id=f.id,
                    metadata={"size_bytes": f.size_bytes, "filename": f.original_filename},
                )
                file_count += 1
            share.state = ShareState.expired
            record_audit_event(
                db,
                event_type=AuditEventType.share_expired,
                actor_user_id=None,
                target_type="share",
                target_id=share.id,
                metadata={"file_count": file_count},
            )
            try:
                db.commit()
            except Exception:
                db.rollback()
                failed_shares += 1
                logger.exception(
                    "expire_files: commit failed for share=%s", share.id
                )
                continue  # nothing purged yet -> safe to retry next run
            expired_shares += 1
            # Post-commit: irreversible byte unlink + best-effort quota release.
            # A failure here leaks bytes on disk (cleaned by orphan-reclaim /
            # disk sweep) but never loses a live file or double-releases quota.
            backend = get_storage_backend()
            for locator, uid, size, was_infected in to_purge:
                if locator:
                    try:
                        backend.delete(locator)
                    except Exception as e:
                        # files-8: the comment claimed orphan-reclaim would mop
                        # this up, but reclaim works from DB rows and the row is
                        # already `deleted`, so nothing could ever see it again.
                        # Record the locator so the sweeper has something to act
                        # on instead of silently leaking the bytes.
                        logger.error(
                            "expire_files: post-commit byte purge failed share=%s "
                            "locator=%s: %s - recording for reclaim",
                            share.id, locator, e,
                        )
                        _record_orphan_locator(db, locator=locator, reason="expire_purge_failed")
                if not was_infected:
                    release_bytes(user_id=uid, bytes_to_free=size)
                deleted_files += 1
        if expired_shares or failed_shares:
            logger.info(
                "expire_files: expired %d shares (%d failed), deleted %d files",
                expired_shares, failed_shares, deleted_files,
            )
        return {
            "expired_shares": expired_shares,
            "deleted_files": deleted_files,
            "failed_shares": failed_shares,
        }
    finally:
        db.close()
