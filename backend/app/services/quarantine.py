"""Quarantine flow - what happens after AV says "infected".

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

from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..utils.timeutil import utc_now
from .audit import record_audit_event
from .quota import release_bytes
from .storage_backend import get_storage_backend

logger = logging.getLogger("fileheron.quarantine")


def _safe_filename(name: str) -> str:
    # Strip path separators and NULs; keep the rest. Quarantine paths
    # never round-trip back to disclose user-controlled bytes anywhere
    # web-facing, so a light scrub is enough.
    return name.replace("/", "_").replace("\\", "_").replace("\0", "")[:240] or "untitled.bin"


def quarantine_file(
    db: Session, *, file: File, signature: str | None = None
) -> str | None:
    """Move the file into quarantine + revoke the share. Returns the
    quarantine locator on success; None if the file was already gone.

    Caller commits."""
    if file.state == FileState.deleted:
        logger.info("quarantine_file: %s already deleted; skipping", file.id)
        return None

    backend = get_storage_backend()
    src_loc = (
        file.storage_path
        if (file.storage_path and backend.exists(file.storage_path))
        else None
    )
    dest_loc: str | None = None
    if src_loc is not None:
        dest_loc = backend.quarantine_locator(
            file.share_id, _safe_filename(file.original_filename)
        )
        # If a stale name collision exists (re-quarantine of same name),
        # append the file id to disambiguate. Not a hot path; readability wins.
        if backend.exists(dest_loc):
            dest_loc = backend.quarantine_locator(
                file.share_id, f"{file.id}__{_safe_filename(file.original_filename)}"
            )

    # Phase 1: mark infected + revoke the share + audit, and COMMIT FIRST - with
    # storage_path still at the original location. This is the security-critical
    # part that must survive. Doing the irreversible on-disk move / quota release
    # BEFORE the commit (the old flow, where the CALLER committed) meant a commit
    # failure left an infected file the DB still believed clean, its bytes already
    # moved. Mirrors expire_files (audit M14).
    file.state = FileState.infected
    record_audit_event(
        db,
        event_type=AuditEventType.file_quarantined,
        actor_user_id=None,  # system action
        target_type="file",
        target_id=file.id,
        metadata={
            "signature": signature,
            "size_bytes": file.size_bytes,
            "filename": file.original_filename,
            "quarantine_path": dest_loc,
        },
    )
    # Revoke the parent share (cascades to all of its files in user-facing
    # listings). Idempotent: if it's already revoked / deleted, no-op.
    share = db.query(Share).filter(Share.id == file.share_id).one_or_none()
    # `pending_approval` counts too. A share awaiting four-eyes review whose
    # content just failed AV was left unrevoked and unannotated, so the approver
    # saw a normal pending share and could flip it live - publishing a share
    # whose member file is infected. The file itself 410s on download, but the
    # approval workflow exists precisely so a human decides with full
    # information, and this withheld the single most relevant fact
    # (audit 2026-07-30).
    if share is not None and share.state in (
        ShareState.active,
        ShareState.pending_approval,
    ):
        share.state = ShareState.revoked
        # Stamp WHEN. This is the only terminal share transition that did not,
        # so the orphan-reclaim grace window for the share's remaining clean
        # files started whenever the nightly cron got round to filling the
        # field in - up to ~18 hours late - and anyone reading `terminated_at`
        # for incident timing saw the cron's clock, not the quarantine's
        # (audit #2).
        if getattr(share, "terminated_at", None) is None:
            share.terminated_at = utc_now()
        record_audit_event(
            db,
            event_type=AuditEventType.share_revoked,
            actor_user_id=None,
            target_type="share",
            target_id=share.id,
            metadata={"reason": "av_quarantine", "trigger_file_id": file.id},
        )
    db.commit()

    # Phase 2: the irreversible side effects, now that the state is durable. A
    # move failure leaves storage_path at the original location (admin tooling
    # still finds the bytes there); the quota release reconciles hourly.
    moved = False
    if src_loc is not None:
        try:
            backend.move(src_loc, dest_loc)
            file.storage_path = dest_loc
            db.commit()
            moved = True
        except OSError as e:
            db.rollback()
            logger.error(
                "quarantine move failed for %s (already marked infected): %s", file.id, e
            )
    release_bytes(user_id=file.uploaded_by_id, bytes_to_free=file.size_bytes)

    # Tell the uploader + (optionally) fan out to admins. Wrapped -
    # never fail the quarantine because of a notification path.
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

        # Optional admin fan-out - gated by the runtime setting. Admin
        # email IS stored in users.email (the early hash+hint design was
        # retired), so we pass `email_to=admin.email` and let each admin's
        # per-category preference (default `both`) decide channel.
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
    except Exception as e:
        logger.exception(
            "could not enqueue file_quarantined notification for file=%s", file.id
        )
        # Surface the dispatch failure via the audit log so the next
        # ops_check sees a recent dispatch_failed event and alerts
        # admins. Belt-and-braces: the file is already quarantined; we
        # just want the operator to know the uploader/admin wasn't told.
        try:
            record_audit_event(
                db,
                event_type=AuditEventType.ops_alert_dispatched,
                actor_user_id=None,
                target_type="file",
                target_id=file.id,
                metadata={"reason": "quarantine_dispatch_failed", "error": str(e)[:300]},
            )
        except Exception:
            logger.exception("could not record dispatch-failed audit for file=%s", file.id)

    return dest_loc if moved else None
