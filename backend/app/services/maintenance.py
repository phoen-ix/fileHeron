"""Maintenance mode (v1.34.0).

When enabled, NEW file transfers are refused with 503 `MAINTENANCE_MODE` while
in-progress ones are allowed to finish; the rest of the app stays usable. Toggled
manually by an admin or automatically by the postpone-update flow (a deferred
self-update stored in `maintenance.pending_update`, fired by the drain worker).

This is the kv + gate layer only; the live "is anything transferring right now"
counters live in `services/transfer_activity.py`, and the deferred-update
orchestration in `workers/drain_pending_update.py`.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.http_range import is_partial_continuation
from . import settings as settings_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.maintenance")


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(db, settings_svc.Keys.MAINTENANCE_ENABLED, default=False)


def get_message(db: Session) -> str:
    return (settings_svc.get(db, settings_svc.Keys.MAINTENANCE_MESSAGE) or "").strip()


def set_enabled(
    db: Session,
    enabled: bool,
    *,
    actor: User | None,
    message: str | None = None,
    request=None,
    audit: bool = True,
) -> None:
    """Flip the flag (and optional banner message). Caller commits."""
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MAINTENANCE_ENABLED,
        value="true" if enabled else "false",
        actor=actor,
        request=request,
    )
    if message is not None:
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.MAINTENANCE_MESSAGE,
            value=message.strip() or None,
            actor=actor,
            request=request,
        )
    if audit:
        record_audit_event(
            db,
            event_type=(
                AuditEventType.maintenance_enabled
                if enabled
                else AuditEventType.maintenance_disabled
            ),
            actor_user_id=actor.id if actor else None,
            target_type="settings",
            target_id="maintenance",
            request=request,
        )


def refuse_if_maintenance(db: Session, *, request=None, kind: str = "transfer") -> None:
    """Raise 503 when maintenance mode blocks a NEW transfer.

    `kind="download"` lets a ranged continuation through - a resumed/ranged GET is
    finishing an in-progress download, not starting a new one, and the whole point
    of maintenance mode is to let in-progress transfers complete.
    """
    if not is_enabled(db):
        return
    if kind == "download" and request is not None and is_partial_continuation(request):
        return
    raise AppError(
        503,
        "MAINTENANCE_MODE",
        get_message(db)
        or "The server is in maintenance mode; new transfers are paused. Please try again shortly.",
    )


# --- deferred (postponed) update record -----------------------------------

def get_pending_update(db: Session) -> dict | None:
    raw = settings_svc.get(db, settings_svc.Keys.MAINTENANCE_PENDING_UPDATE)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("maintenance.pending_update unparseable; ignoring")
        return None


def set_pending_update(db: Session, record: dict | None, *, actor: User | None) -> None:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MAINTENANCE_PENDING_UPDATE,
        value=json.dumps(record) if record is not None else None,
        actor=actor,
    )


def apply_pending_update(
    db: Session, *, actor: User | None = None, request=None, reason: str = "drain"
) -> dict | None:
    """Fire the deferred update: clear maintenance + the pending record (committed
    first so the freshly-updated container never boots stuck in maintenance), then
    hand the tag to the updater. Used by the drain worker and the admin
    'update now' control. Returns the job dict, or None if nothing is pending."""
    pending = get_pending_update(db)
    if not pending:
        return None
    set_enabled(db, False, actor=actor, audit=False)
    set_pending_update(db, None, actor=actor)
    db.commit()

    from .release_apply import apply as _release_apply
    result = _release_apply(action="update", target_tag=pending["target_tag"])
    record_audit_event(
        db,
        event_type=AuditEventType.update_triggered,
        actor_user_id=actor.id if actor else None,
        target_type="update_job",
        target_id=result["job_id"],
        metadata={"target_tag": pending["target_tag"], "via": reason},
        request=request,
    )
    db.commit()
    logger.info("pending update applied: tag=%s via=%s", pending["target_tag"], reason)
    return result


def cancel_pending_update(db: Session, *, actor: User | None, request=None) -> bool:
    """Cancel a postponed update and leave maintenance mode. Returns True if there
    was something to cancel."""
    had_pending = get_pending_update(db) is not None
    set_pending_update(db, None, actor=actor)
    set_enabled(db, False, actor=actor, audit=False)
    record_audit_event(
        db,
        event_type=AuditEventType.update_postpone_cancelled,
        actor_user_id=actor.id if actor else None,
        target_type="update_job",
        target_id=None,
        request=request,
    )
    db.commit()
    return had_pending
