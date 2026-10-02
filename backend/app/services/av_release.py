"""Releasing a scanned file: the ONE flip from `ready_unscanned` to `clean`.

Two verdicts reach it - `clean`, or `unscanned:<reason>` (a file clamd cannot or
will not vouch for, served with an honest label: `av_unscanned` and a
`file_served_unscanned` audit row in the same transaction). The flip is
conditional on the row still being `ready_unscanned`: a slow scan can run while
share expiry commits `deleted` and frees the bytes, and flipping then would
resurrect a file whose bytes are gone.

With encryption at rest on, the release lane folds its swap into this same
UPDATE (`extra_values` / `extra_where`): the file becomes downloadable and
ciphertext in one statement, so there is no moment where it is clean and
plaintext. Never commits - the caller does, and then notifies.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import settings
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..utils.dbresult import updated_rows
from .audit import record_audit_event

logger = logging.getLogger("fileheron.av_release")

VERDICT_CLEAN = "clean"
_UNSCANNED_PREFIX = "unscanned:"


def unscanned(reason: str) -> str:
    return f"{_UNSCANNED_PREFIX}{reason}"


def apply_verdict(
    db: Session,
    file: File,
    verdict: str,
    *,
    extra_values: dict[str, Any] | None = None,
    extra_where: list[Any] | None = None,
) -> bool:
    """Flip `file` to clean under `verdict`. False when the row had already left
    `ready_unscanned` (or an `extra_where` no longer holds): nothing changed and
    the caller rolls back."""
    is_unscanned = verdict.startswith(_UNSCANNED_PREFIX)
    if not is_unscanned and verdict != VERDICT_CLEAN:
        raise ValueError(f"unknown verdict {verdict!r}")
    stmt = (
        update(File)
        .where(File.id == file.id, File.state == FileState.ready_unscanned, *(extra_where or []))
        .values(state=FileState.clean, av_unscanned=is_unscanned, **(extra_values or {}))
        .execution_options(synchronize_session=False)
    )
    if updated_rows(db.execute(stmt)) == 0:
        return False
    if is_unscanned:
        record_audit_event(
            db,
            event_type=AuditEventType.file_served_unscanned,
            actor_user_id=file.uploaded_by_id,
            target_type="file",
            target_id=file.id,
            metadata={
                "size_bytes": file.size_bytes,
                "av_max_scan_bytes": settings.AV_MAX_SCAN_BYTES,
                "reason": verdict[len(_UNSCANNED_PREFIX):],
            },
        )
    return True


def notify_recipients_if_ready(db: Session, *, share_id: str | None) -> None:
    """A file just became downloadable: send whatever recipient mail was waiting
    for that - the share's announcement or an owed "files added" notice
    (v2.23.0, `share.notify_if_downloadable`). The minute sweep is the fallback.
    Never raises: the release is already committed, and a mail that fails here
    is sent by the sweep instead."""
    if not share_id:
        return
    try:
        from . import share as share_svc

        # Commit either way: a False return has written nothing, and a
        # rollback would expire every object the session holds for no reason.
        share_svc.notify_if_downloadable(db, share_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("recipient notice for share %s failed", share_id)
