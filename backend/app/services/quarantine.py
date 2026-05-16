"""Quarantine flow — what happens after AV says "infected".

Steps:
1. Move the on-disk file from STORAGE_ROOT/.../{file_id}.bin to
   QUARANTINE_DIR/{share-uuid}/{original_filename}.
2. Update files row → state=infected (storage_path stays so admins can
   inspect / re-scan).
3. Revoke the parent share + every public link on it.
4. Release the quota reservation (we keep the file on disk under
   quarantine, but the user shouldn't pay for malware they uploaded).
5. Audit log: file_quarantined per file + share_revoked per share.

This is reversible: the bytes still exist under quarantine. The admin
release/purge/download flow lives in services/quarantine_admin.py +
routers/admin.py.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from .audit import record_audit_event
from .quota import release_bytes

logger = logging.getLogger("fileheron.quarantine")


def _safe_filename(name: str) -> str:
    # Strip path separators and NULs; keep the rest. Quarantine paths
    # never round-trip back to disclose user-controlled bytes anywhere
    # web-facing, so a light scrub is enough.
    return name.replace("/", "_").replace("\\", "_").replace("\0", "")[:240] or "untitled.bin"


def quarantine_file(
    db: Session, *, file: File, signature: str | None = None
) -> Path | None:
    """Move the file into quarantine + revoke the share. Returns the
    quarantine path on success; None if the file was already gone.

    Caller commits."""
    if file.state == FileState.deleted:
        logger.info("quarantine_file: %s already deleted; skipping", file.id)
        return None

    src = Path(file.storage_path) if file.storage_path else None
    dest: Path | None = None
    moved = False
    move_error: str | None = None
    if src is not None and src.is_file():
        dest_dir = Path(settings.QUARANTINE_DIR) / file.share_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _safe_filename(file.original_filename)
        # If a stale name collision exists (re-quarantine of same name),
        # append the file id to disambiguate. Not a hot path; readability wins.
        if dest.exists():
            dest = dest_dir / f"{file.id}__{_safe_filename(file.original_filename)}"
        try:
            os.rename(str(src), str(dest))
            moved = True
        except OSError as e:
            move_error = str(e)
            logger.error("quarantine move failed for %s: %s", file.id, e)
            # Fall through — we still mark the file infected even if the
            # move failed; admins can clean disk manually. The DB row's
            # storage_path stays at the pre-quarantine location (NOT the
            # would-be dest) so admin tooling can still find the bytes.

    file.state = FileState.infected
    if moved and dest is not None:
        file.storage_path = str(dest)
    # else: leave storage_path unchanged — points at the original location
    # (or remains None if there was no file on disk to begin with).
    db.flush()

    # Release uploader's quota — they shouldn't be charged for the bytes
    # we just moved out of their share into the org's quarantine bucket.
    release_bytes(user_id=file.uploaded_by_id, bytes_to_free=file.size_bytes)

    audit_meta: dict = {
        "signature": signature,
        "size_bytes": file.size_bytes,
        "filename": file.original_filename,
        "quarantine_path": str(dest) if moved and dest else None,
    }
    if move_error is not None:
        audit_meta["move_failed"] = True
        audit_meta["move_error"] = move_error
    record_audit_event(
        db,
        event_type=AuditEventType.file_quarantined,
        actor_user_id=None,  # system action
        target_type="file",
        target_id=file.id,
        metadata=audit_meta,
    )

    # Revoke the parent share (cascades to all of its files in user-facing
    # listings). Idempotent: if it's already revoked / deleted, no-op.
    share = db.query(Share).filter(Share.id == file.share_id).one_or_none()
    if share is not None and share.state == ShareState.active:
        share.state = ShareState.revoked
        record_audit_event(
            db,
            event_type=AuditEventType.share_revoked,
            actor_user_id=None,
            target_type="share",
            target_id=share.id,
            metadata={"reason": "av_quarantine", "trigger_file_id": file.id},
        )

    # Phase 6a: tell the uploader. Wrapped — never fail the quarantine
    # because of a notification path.
    try:
        from ..models.notification import NotificationCategory
        from ..models.user import User, UserRole
        from . import notification as notif_svc
        from . import settings as settings_svc

        uploader = db.query(User).filter(User.id == file.uploaded_by_id).one_or_none()
        payload = {
            "uploader_name": uploader.display_name if uploader else "(unknown)",
            "filename": file.original_filename,
            "signature": signature or "unknown",
        }
        if uploader is not None and not uploader.is_disabled:
            notif_svc.dispatch(
                db,
                user=uploader,
                category=NotificationCategory.file_quarantined,
                payload=payload,
                email_to=uploader.email,
            )

        # Optional admin fan-out — gated by the runtime setting. In-app
        # bell only (system has no plaintext admin email).
        if settings_svc.get_bool(
            db, settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, default=False
        ):
            uploader_id = uploader.id if uploader is not None else None
            admins = (
                db.query(User)
                .filter(User.role == UserRole.admin, User.is_disabled == False)  # noqa: E712
                .all()
            )
            for admin in admins:
                # Don't double-notify if the uploader IS an admin.
                if admin.id == uploader_id:
                    continue
                notif_svc.dispatch(
                    db,
                    user=admin,
                    category=NotificationCategory.file_quarantined,
                    payload=payload,
                    email_to=admin.email,
                )
    except Exception:
        logger.exception(
            "could not enqueue file_quarantined notification for file=%s", file.id
        )

    return dest if moved else None
