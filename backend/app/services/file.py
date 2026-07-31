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
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
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
    are skipped (not an error); a missing-on-disk row is skipped + logged."""
    out: list[File] = []
    backend = get_storage_backend()
    rows = (
        db.query(File)
        .filter(File.share_id == share_id, File.state == FileState.clean)
        .order_by(File.created_at.asc())
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
    """
    record = File(
        share_id=share.id,
        original_filename=safe_original_filename(original_filename),
        mime_type=(mime_type or "application/octet-stream")[:255],
        size_bytes=size_bytes,
        state=FileState.uploading,
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
    db.flush()

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
) -> None:
    """Hard-delete from disk + DB row marker.

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
        return

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

    if file.storage_path:
        get_storage_backend().delete(file.storage_path)

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
            target_id=locator[:255],
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
