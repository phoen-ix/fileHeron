"""File records - create / finalize / delete.

Storage layout: ``{STORAGE_ROOT}/{yyyy}/{mm}/{file_id}.bin``
                e.g. /data/files/2026/05/abc-123-...-def.bin

Finalize uses ``shutil.move`` so it works whether STORAGE_ROOT and
TUS_UPLOAD_DIR are on the same filesystem (atomic rename) or not
(fall back to copy + unlink). Docker bind mounts often appear as
distinct filesystems inside the container, hence the fallback.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType, AuditLog
from ..models.download_log import DownloadLog
from ..models.file import File, FileApprovalState, FileState
from ..models.share import Share, ShareState
from ..models.user import User
from ..utils.timeutil import utc_now
from .audit import record_audit_event
from .quota import release_bytes
from .storage_backend import get_storage_backend

logger = logging.getLogger("fileheron.file")




def storage_path_for(file_id: str, when: datetime | None = None) -> Path:
    """Deterministic local on-disk path for a finalized file. Thin wrapper over
    the active backend's locator (local backend → the same path as always)."""
    return Path(get_storage_backend().generate_locator(file_id, when))


def downloadable_files(db: Session, share_id: str) -> list[File]:
    """The `clean` files of a share whose bytes are actually present on disk -
    the set a bulk-ZIP download includes. Scanning / infected / deleted files
    are skipped (not an error); a missing-on-disk row is skipped + logged.

    The order is TOTAL, not just `created_at`: files uploaded in the same second
    tie, and MariaDB is free to break a tie differently between two queries. The
    bulk ZIP is resumable, which means the same share must lay its members out
    identically on every request - a reordering mid-resume would splice two
    archives into a corrupt one (audit 2026-07-30)."""
    out: list[File] = []
    backend = get_storage_backend()
    rows = (
        db.query(File)
        .filter(
            File.share_id == share_id,
            File.state == FileState.clean,
            # A file still awaiting its own four-eyes decision is not part of
            # the archive. This is the recipient-facing artifact and the public
            # link consumes it too, so the exclusion is unconditional rather
            # than viewer-dependent - a per-viewer member list would also make
            # the ZIP non-reproducible and break resume.
            File.approval_state == FileApprovalState.approved,
        )
        .order_by(File.created_at.asc(), File.id.asc())
        .all()
    )
    for f in rows:
        if f.storage_path and backend.exists(f.storage_path):
            out.append(f)
        else:
            logger.error("downloadable_files: %s missing in storage: %r", f.id, f.storage_path)
    return out


def safe_original_filename(name: str) -> str:
    """Reduce a client-supplied filename to a safe leaf: strip any directory
    components (both / and \\ - a Windows client may send back-slashes), drop
    control/NUL chars, and map the traversal specials ('', '.', '..') to a
    placeholder. Prevents a path-traversal payload from ever being persisted,
    so no downstream consumer (the desktop 'Save all', a ZIP entry name, a
    Content-Disposition header) can be tricked into escaping its target dir
    (audit H4, backend defense-in-depth)."""
    leaf = (name or "").replace("\\", "/").split("/")[-1]
    leaf = "".join(ch for ch in leaf if ch >= " ").strip()
    if leaf in ("", ".", ".."):
        return "file"
    return leaf[:512]


def create_pending(
    db: Session,
    *,
    share: Share,
    uploader: User,
    original_filename: str,
    mime_type: str,
    size_bytes: int,
) -> File:
    """Insert a `files` row in state=uploading. Returns the row (caller
    commits).

    A file landing on a share that was four-eyes-gated and is ALREADY past its
    decision needs its own review before recipients can reach it - otherwise the
    owner appends to an approved share and the payload ships unreviewed. While
    the share is still `pending_approval` the pending decision covers it, so it
    arrives `approved` and the share-level gate does the work.
    """
    needs_review = (
        share.approval_was_required and share.state == ShareState.active
    )
    record = File(
        share_id=share.id,
        original_filename=safe_original_filename(original_filename),
        mime_type=(mime_type or "application/octet-stream")[:255],
        size_bytes=size_bytes,
        state=FileState.uploading,
        approval_state=(
            FileApprovalState.pending_review
            if needs_review
            else FileApprovalState.approved
        ),
        uploaded_by_id=uploader.id,
    )
    db.add(record)
    db.flush()
    return record


def finalize_to_disk(
    db: Session, *, file: File, tus_upload_id: str, request=None
) -> Path:
    """Move the tusd working file into permanent storage. Update the row's
    state, storage_path, finalized_at, and clear tus_upload_id.

    The tusd working file is at ``{TUS_UPLOAD_DIR}/{tus_upload_id}``. (No
    extension; tusd uses raw binary names.)

    Side effects:
    - Creates target subdirectories if needed.
    - Atomic rename when TUS_UPLOAD_DIR and STORAGE_ROOT share a
      filesystem; otherwise shutil.move falls back to copy + unlink
      (Docker bind mounts often look cross-device inside the
      container even on the same host disk - Errno 18 EXDEV).
    """
    src = Path(settings.TUS_UPLOAD_DIR) / tus_upload_id
    if not src.is_file():
        # tusd may store metadata as `<id>.info`. The data file is just <id>.
        # If neither exists, the upload didn't actually land - bail out.
        raise AppError(500, "UPLOAD_MISSING", f"tusd upload {tus_upload_id} not found on disk.")

    when = utc_now()
    backend = get_storage_backend()
    locator = backend.generate_locator(file.id, when)

    # Write the intended locator DOWN before moving the bytes, and commit it.
    #
    # `backend.finalize` consumes the tusd working file - a rename on the same
    # filesystem, a copy+unlink across one. It is irreversible. Doing it before
    # any commit meant a commit failure right after (a DB blip, a lock timeout,
    # the connection dropping) left the bytes sitting at `locator` while the row
    # still said `uploading` with `storage_path=NULL`. Nothing could find them
    # again: the sweeper looks for tusd working files, which are gone;
    # reclaim_orphaned_files walks `files` rows, which point nowhere. The
    # uploader's quota stayed charged for bytes no one could serve or delete
    # (audit 2026-07-30).
    #
    # Recording the intent first makes the failure recoverable: the row now
    # names the locator, so the orphan sweeper has something to act on whichever
    # side of the move the failure lands.
    file.storage_path = locator
    # COMMIT, not flush. A flush is invisible to everyone outside this
    # transaction, so the "recording the intent first makes the failure
    # recoverable" above was not true: a rollback took the locator with it and
    # left the bytes exactly as orphaned as before - while the tusd working
    # file, the only other way to find them, had already been consumed by the
    # move (audit #2). Committed, the row names the locator, so
    # `cleanup_stale_uploads` finds an `uploading` row past its retention
    # window and deletes the bytes it points at.
    #
    # Safe to commit here: the caller (tus_hooks.handle_post_finish) has
    # nothing else pending - `tus_upload_id` was committed by the pre-finish
    # hook - so this commits the locator and only the locator.
    db.commit()

    backend.finalize(str(src), locator)

    # tusd also writes a sidecar .info - clean it up.
    info_sidecar = src.with_suffix(".info")
    if info_sidecar.is_file():
        try:
            info_sidecar.unlink()
        except OSError:
            pass

    file.finalized_at = when
    file.state = FileState.ready_unscanned
    file.tus_upload_id = None
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.file_finalized,
        actor_user_id=file.uploaded_by_id,
        target_type="file",
        target_id=file.id,
        metadata={"size_bytes": file.size_bytes, "filename": file.original_filename},
        request=request,
    )
    logger.info("file finalized id=%s locator=%s size=%d", file.id, locator, file.size_bytes)
    return Path(locator)


def hard_delete(
    db: Session,
    *,
    file: File,
    reason: str = "user_request",
    actor_user_id: int | None = None,
    request=None,
    purge: bool = True,
) -> str | None:
    """Hard-delete from disk + DB row marker.

    With `purge=False` the bytes are NOT unlinked; the locator is returned and
    the caller must unlink it AFTER committing. That is the ordering v2.5.0
    established for share expiry and the one to prefer: unlinking first means a
    commit that then fails (a lock-wait timeout, a dropped connection) rolls the
    row back to `clean` with a `storage_path` pointing at nothing - the file
    shows as present in the admin browser, a download 500s out of FileResponse,
    and the next sweep releases the same bytes from the quota counter a second
    time (audit #2).

    `purge=True` (the default) keeps the old ordering where the caller must
    learn that an unlink FAILED before it does anything else: GDPR erasure, so
    it does not write a receipt claiming the data is gone, and the config-import
    purge, which reports per-user outcomes. Everything else - the interactive
    delete routes and the orphan-reclaim cron - defers (audit #2 cross-check
    corrected this note, which named a single caller).

    `actor_user_id` defaults to the uploader (a self-service delete); pass an
    admin's id for an admin-initiated delete so the audit records the real
    actor, not the file's owner.

    Raises OSError if the disk unlink fails - callers MUST decide whether
    to abort the surrounding transaction (e.g. GDPR erasure: stop, don't
    lie in the receipt PDF) or to swallow + audit + continue (e.g. cron
    expire: keep processing remaining shares). Pre-fix this function
    silently swallowed unlink failures, leaving file rows marked deleted
    while the bytes leaked on disk.
    """
    # Idempotency guard: a second hard_delete on an already-deleted file
    # would re-run release_bytes and double-credit the uploader's quota
    # (finding L11). Bail out - the row is already a deleted marker.
    if file.state == FileState.deleted:
        return None

    # Capture the pre-delete state so we don't double-release quota for
    # files that already went through quarantine (services/quarantine.py
    # released the bytes when it moved the file into ./data/quarantine/).
    was_infected = file.state == FileState.infected

    # Only release what was actually reserved. Quota is reserved at the tus
    # pre-create hook (tus_hooks.py) or by the direct-upload route - NOT by
    # /api/uploads/init, which just writes the `uploading` row. So a row that
    # was registered and then abandoned before tusd ever accepted a byte holds
    # no reservation, and releasing on delete pushed the Redis counter BELOW
    # true usage - repeatably, which is a quota bypass (audit 2026-07-30).
    #
    # `tus_upload_id` is the marker that pre-create ran, which is the same
    # discriminator cleanup_stale_uploads already documents for skipping the
    # release entirely. Anything past `uploading` reached a finalized state and
    # therefore reserved. If this is ever wrong in either direction, the hourly
    # quota_reconcile recomputes from disk and is the source of truth.
    reserved = file.state != FileState.uploading or file.tus_upload_id is not None

    deferred: str | None = None
    if file.storage_path:
        if purge:
            get_storage_backend().delete(file.storage_path)
        else:
            deferred = file.storage_path

    file.state = FileState.deleted
    db.flush()

    if not was_infected and reserved:
        release_bytes(user_id=file.uploaded_by_id, bytes_to_free=file.size_bytes)

    record_audit_event(
        db,
        event_type=AuditEventType.file_deleted,
        actor_user_id=actor_user_id if actor_user_id is not None else file.uploaded_by_id,
        target_type="file",
        target_id=file.id,
        metadata={"reason": reason, "size_bytes": file.size_bytes},
        request=request,
    )
    return deferred


def purge_locators(
    db: Session, locators: list[str | None], *, reason: str
) -> list[str]:
    """Unlink bytes AFTER the caller's commit. Returns the locators that could
    NOT be unlinked.

    Never raises - the rows are already committed as `deleted`, and raising
    would 500 a delete that has already happened. But it must not stay silent
    either: `reclaim_orphaned_files` works from DB rows and only looks at
    `clean`/`ready_unscanned` ones, so a `deleted` row's locator is unreachable
    the moment this fails. A log line is not durable (container stdout rotates,
    and a bare `logger.error` reaches neither `error_log` nor an alert), so each
    failure gets a `file_purge_failed` audit row - exactly what
    `purge_expired_bytes` has done since v2.5.0.

    This function's own docstring used to claim "a failure here leaks bytes
    that the orphan sweeper can still find" while the comment inside its except
    block said the opposite. The except block was right.
    """
    backend = get_storage_backend()
    failed: list[str] = []
    for locator in locators:
        if not locator:
            continue
        try:
            backend.delete(locator)
        except Exception:
            logger.error(
                "deferred purge FAILED for %s - the row is deleted, so these bytes "
                "are now orphaned and no sweeper will find them",
                locator,
                exc_info=True,
            )
            failed.append(locator)
            record_orphan_locator(db, locator=locator, reason=reason)
    return failed


def revoke_share_if_empty(
    db: Session,
    *,
    share_id: str,
    just_deleted_file_id: str,
    actor_user_id: int,
    request=None,
) -> None:
    """If the share has no remaining non-deleted files, revoke it (a share with
    nothing left to download is dead). Shared by owner-delete and admin-delete.
    Caller commits."""
    share = db.query(Share).filter(Share.id == share_id).one_or_none()
    if share is None or share.state != ShareState.active:
        return
    remaining = (
        db.query(File)
        .filter(
            File.share_id == share_id,
            File.state != FileState.deleted,
            File.id != just_deleted_file_id,
        )
        .count()
    )
    if remaining == 0:
        share.state = ShareState.revoked
        share.terminated_at = utc_now()
        record_audit_event(
            db,
            event_type=AuditEventType.share_revoked,
            actor_user_id=actor_user_id,
            target_type="share",
            target_id=share.id,
            metadata={"reason": "last_file_deleted"},
            request=request,
        )


# (locator, uploader_id, size_bytes) queued for purge after the caller commits.
# `locator` is None for an infected file: its bytes were MOVED to quarantine and
# its quota already released, so both must be left alone.
PurgeEntry = tuple[str | None, int, int]


def has_recent_counted_download(
    db: Session, *, file_id: str, user_id: int, within_hours: int
) -> bool:
    """Whether this user already paid for a download of this file recently.

    The evidence a ranged continuation needs on the AUTHENTICATED path. The
    public path uses a short Redis mark (browser resumes happen in seconds), but
    the desktop client can pause a download and resume it the next day, so this
    is measured in hours and read from `download_log` - a durable row written
    when the download was counted. It survives a Redis restart, and it grants
    nothing at all to a caller who never downloaded the file (audit 2026-07-30,
    the authenticated sibling of flow-publiclink-7)."""
    cutoff = utc_now() - timedelta(hours=max(1, within_hours))
    return (
        db.query(DownloadLog.id)
        .filter(
            DownloadLog.file_id == file_id,
            DownloadLog.accessed_by_user_id == user_id,
            DownloadLog.accessed_at >= cutoff,
        )
        .first()
        is not None
    )


def mark_deleted_for_expiry(db: Session, *, file: File) -> PurgeEntry | None:
    """Phase 1 of expiring a file: flip the row to `deleted` and audit it,
    inside the CALLER'S transaction. Returns what phase 2 must purge, or None
    if the file was already deleted.

    Nothing irreversible happens here. `delete_file_for_expiry`, which this
    replaces, unlinked the bytes and released the Redis quota counter BEFORE the
    caller's commit - so a commit failure left a row still marked `clean` whose
    bytes were already gone (silent data loss the UI could not show), and the
    next run released the same bytes a second time. The hourly cron was
    restructured for exactly this in audit M14; the owner-driven paths were not
    (audit 2026-07-30)."""
    if file.state == FileState.deleted:
        return None

    # An infected file already had its bytes moved to quarantine and its quota
    # released by services/quarantine.py; re-releasing here would double-credit
    # the uploader (mirrors the hard_delete guard above, finding L11).
    was_infected = file.state == FileState.infected
    entry: PurgeEntry = (
        None if was_infected else file.storage_path,
        file.uploaded_by_id,
        file.size_bytes,
    )

    file.state = FileState.deleted
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.file_expired,
        actor_user_id=None,
        target_type="file",
        target_id=file.id,
        metadata={"size_bytes": file.size_bytes, "filename": file.original_filename},
    )
    return entry


# audit_log.target_id is String(64); this used to clip to 255, i.e. FOUR times
# the column width, so a locator longer than the column raised DataError under
# MariaDB strict mode - and record_orphan_locator's own `except` swallowed it,
# losing the durable orphan trace its docstring says a log line cannot replace.
# The default local locator (~60 chars) fits; a longer STORAGE_ROOT or an S3
# prefix does not.
_AUDIT_TARGET_ID_MAX = AuditLog.__table__.c.target_id.type.length


def record_orphan_locator(db: Session, *, locator: str, reason: str) -> None:
    """Leave a durable trace of bytes that failed to unlink.

    `reclaim_orphaned_files` works from DB rows, and the row is already
    `deleted` by the time a purge runs - so without this the locator is
    unreachable and the bytes leak silently, charged to nobody and visible to
    no one. Commits on its own: it runs after the caller's transaction closed."""
    try:
        record_audit_event(
            db,
            event_type=AuditEventType.file_purge_failed,
            actor_user_id=None,
            target_type="file_bytes",
            target_id=locator[:_AUDIT_TARGET_ID_MAX],
            metadata={"locator": locator, "reason": reason},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("could not record orphan locator %s", locator)


def purge_expired_bytes(
    db: Session, entries: list[PurgeEntry], *, reason: str
) -> int:
    """Phase 2: unlink bytes and release quota. MUST run only after the
    caller's commit succeeded - both effects are irreversible and neither is
    transactional. Returns how many entries were processed; a failed unlink is
    recorded as an orphan locator rather than raised, so one bad file does not
    abort the rest."""
    backend = get_storage_backend()
    for locator, uploader_id, size_bytes in entries:
        if locator is None:
            continue  # infected: bytes are in quarantine, quota already released
        try:
            backend.delete(locator)
        except Exception as e:
            logger.error(
                "post-commit byte purge failed locator=%s: %s - recording for "
                "reclaim", locator, e,
            )
            record_orphan_locator(db, locator=locator, reason=reason)
        # Released either way: the row is already `deleted`, so the quota must
        # stop counting it whether or not the unlink landed.
        release_bytes(user_id=uploader_id, bytes_to_free=size_bytes)
    return len(entries)
