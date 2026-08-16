"""Admin actions on quarantined files: release back to the owner, or
purge the bytes from disk while keeping the historical row.

Both actions require an `infected` file. Both audit with the admin as
actor and a free-text reason that the admin types into the SPA confirm
dialog (10-500 chars, validated at the schema layer).

Caller commits in both operations.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType, AuditLog
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..models.user import User
from .audit import record_audit_event
from .quota import reserve_bytes
from .storage_backend import get_storage_backend

logger = logging.getLogger("fileheron.quarantine_admin")


def _refuse_unless_infected(file: File) -> None:
    if file.state != FileState.infected:
        raise AppError(
            409,
            "FILE_NOT_INFECTED",
            "Only quarantined (infected) files support this action.",
            details={"current_state": file.state.value},
        )


def release(
    db: Session, *, admin: User, file: File, reason: str, request=None
) -> None:
    """Move bytes back to active storage, reset file → clean, restore the
    parent share if it was revoked specifically by AV (not by an admin
    after the fact), and re-reserve uploader quota."""
    _refuse_unless_infected(file)

    # Move bytes back to active storage via the backend (local: rename/copy;
    # object store: server-side copy between prefixes).
    backend = get_storage_backend()
    if file.storage_path and backend.exists(file.storage_path):
        old_loc = file.storage_path
        new_loc = backend.generate_locator(file.id)
        backend.move(old_loc, new_loc)
        file.storage_path = new_loc
        # Put the bytes BACK if the transaction does not survive. The commit is
        # the caller's (routers/admin/quarantine.py), and between here and it
        # sit a quota reservation, an audit query and a share flip - all able to
        # fail. On rollback the row keeps `old_loc` while the bytes sit at
        # `new_loc`, so every retry finds nothing at `storage_path` and 409s
        # QUARANTINE_BYTES_MISSING below: release becomes impossible from the
        # UI, permanently. The blob is unreclaimable too - reclaim_orphaned_files
        # walks File rows, so a locator no row names is invisible to it.
        #
        # `quarantine.py:70-75` fixed the mirror of this by committing first;
        # that restructure reorders the quota and share work here, so this takes
        # the compensating-move route instead (the shape inbound_mail.py uses).
        from ..database import run_after_rollback

        def _move_back() -> None:
            try:
                if backend.exists(new_loc):
                    backend.move(new_loc, old_loc)
            except Exception:
                logger.warning(
                    "quarantine release: could not restore %s after rollback",
                    old_loc, exc_info=True,
                )

        run_after_rollback(db, _move_back)
    else:
        # Bytes already gone (manual cleanup, prior purge, etc.) - refuse
        # rather than silently flip state on a missing file. Admin should
        # purge instead.
        raise AppError(
            409,
            "QUARANTINE_BYTES_MISSING",
            "Cannot release: the quarantined bytes are no longer on disk.",
        )

    file.state = FileState.clean
    db.flush()

    # Re-reserve uploader quota (release_bytes was called when the file
    # entered quarantine). The flush above already put this row back into
    # STORED_STATES, so it has to be excluded from a lazy counter seed or the
    # seed and the reservation would each charge the same bytes.
    uploader = db.query(User).filter(User.id == file.uploaded_by_id).one_or_none()
    if uploader is not None:
        try:
            reserve_bytes(
                db,
                user=uploader,
                additional_bytes=file.size_bytes,
                exclude_file_id=file.id,
            )
        except AppError:
            # If the uploader is now over quota (admin tightened it after
            # the upload), still allow the release - the bytes already
            # exist on disk; rejecting would leave the file in a
            # half-released state. Log it for ops awareness.
            logger.warning(
                "release: uploader %d would exceed quota after re-reservation; "
                "allowing the release anyway",
                uploader.id,
            )

    # Restore the parent share - but only if it was revoked specifically
    # because of THIS quarantine, not by a separate admin action.
    share = db.query(Share).filter(Share.id == file.share_id).one_or_none()
    share_restored = False
    if share is not None and share.state == ShareState.revoked:
        last_revoke = (
            db.query(AuditLog)
            .filter(
                AuditLog.event_type == AuditEventType.share_revoked.value,
                AuditLog.target_type == "share",
                AuditLog.target_id == share.id,
            )
            .order_by(AuditLog.created_at.desc())
            .first()
        )
        if (
            last_revoke is not None
            and isinstance(last_revoke.extra, dict)
            and last_revoke.extra.get("reason") == "av_quarantine"
            and last_revoke.extra.get("trigger_file_id") == file.id
        ):
            share.state = ShareState.active
            share_restored = True

    record_audit_event(
        db,
        event_type=AuditEventType.file_quarantine_released,
        actor_user_id=admin.id,
        target_type="file",
        target_id=file.id,
        metadata={
            "reason": reason,
            "size_bytes": file.size_bytes,
            "filename": file.original_filename,
            "share_id": file.share_id,
            "share_restored": share_restored,
        },
        request=request,
    )


def purge(
    db: Session, *, admin: User, file: File, request=None
) -> None:
    """Unlink the quarantined bytes from disk and transition the file
    row to ``state=deleted`` so it disappears from /admin/quarantine
    (which filters to ``state=infected``). The ``file_quarantine_purged``
    audit row preserves the history; /admin/file-history still surfaces
    the row under state=deleted."""
    _refuse_unless_infected(file)

    if file.storage_path:
        try:
            get_storage_backend().delete(file.storage_path)
        except Exception as e:
            logger.warning("purge: could not delete %s: %s", file.storage_path, e)
    file.storage_path = None
    file.state = FileState.deleted
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.file_quarantine_purged,
        actor_user_id=admin.id,
        target_type="file",
        target_id=file.id,
        metadata={
            "size_bytes": file.size_bytes,
            "filename": file.original_filename,
            "share_id": file.share_id,
        },
        request=request,
    )
