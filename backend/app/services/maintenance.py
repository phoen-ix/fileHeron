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
from ..utils.timeutil import utc_now
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


def refuse_if_maintenance(
    db: Session, *, request=None, kind: str = "transfer", file_id: str | None = None
) -> None:
    """Raise 503 when maintenance mode blocks a NEW transfer.

    `kind="download"` lets a ranged continuation through - a resumed/ranged GET
    is finishing an in-progress download, not starting a new one, and the whole
    point of maintenance mode is to let in-progress transfers complete.

    That exemption used to be granted on the SHAPE of the header alone, so
    `Range: bytes=1-` on a brand-new connection walked straight past the gate -
    a one-header bypass of the control that pauses transfers before an update
    (audit 2026-07-30, config-7). It now also requires this instance to have
    actually started serving that file inside
    `transfer_activity.RECENT_DOWNLOAD_TTL_SEC`, which is what makes
    "continuation" a claim we can check rather than one we take on trust.

    The download BUDGET is corroborated too, and has been since v2.6.0 - but on
    DIFFERENT evidence, and the difference is load-bearing. This gate asks "did
    this instance serve bytes for this file recently", which is the right
    question for a drain and the wrong one for a budget: it is not keyed on who
    is asking, so under it one principal's activity corroborated another's. The
    budget asks "has THIS principal already PAID", via
    `transfer_activity.was_download_paid` on the anonymous paths and a recent
    `download_log` row on the authenticated ones.

    Do not "restore consistency" by pointing the budget back at this mark. This
    docstring used to claim the opposite - that leaving the budget uncorroborated
    was "a documented, accepted tradeoff", justified by a phone switching
    networks. Both halves were wrong: the tradeoff was closed in v2.6.0, and the
    network-switch reasoning never applied, because the mark is keyed on the
    file and has never looked at the client. It was corrected in the release
    notes, in CLAUDE.md and in the audit record, and this copy was missed - which
    left the only surviving statement of the retracted reasoning sitting in the
    first file anyone opens when working on this gate.
    """
    if not is_enabled(db):
        return
    if kind == "download" and request is not None and is_partial_continuation(request):
        if file_id is None:
            return
        from . import transfer_activity

        if transfer_activity.was_download_recent(file_id):
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


# How long a handed-off update may leave the gate shut before the drain worker
# decides no new container is coming and lifts it. Generously above a normal
# pull+restart; the failure it bounds is "the executor died", not "the pull is
# slow".
HANDOFF_STALE_MIN = 30


def set_handoff_at(db: Session, value: str | None, *, actor: User | None = None) -> None:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MAINTENANCE_UPDATE_HANDOFF_AT,
        value=value,
        actor=actor,
    )


def get_handoff_at(db: Session) -> str | None:
    return settings_svc.get(db, settings_svc.Keys.MAINTENANCE_UPDATE_HANDOFF_AT)


def clear_maintenance_after_update(db: Session) -> bool:
    """Lift maintenance once an update hand-off has concluded.

    Called from the backend's boot (the new container IS the conclusion) and
    from the drain worker (which covers the hand-off that never produced one).
    No-op unless maintenance is on with nothing pending. Returns whether it
    lifted anything."""
    if not is_enabled(db):
        return False
    if get_pending_update(db) is not None:
        return False
    if get_handoff_at(db) is None:
        # Maintenance an operator turned on by hand: not ours to lift.
        return False
    set_enabled(db, False, actor=None, audit=False)
    set_handoff_at(db, None, actor=None)
    db.commit()
    logger.info("maintenance lifted after update hand-off")
    return True


def apply_pending_update(
    db: Session, *, actor: User | None = None, request=None, reason: str = "drain"
) -> dict | None:
    """Fire the deferred update: clear the pending record, keep maintenance ON,
    and hand the tag to the updater. Used by the drain worker and the admin
    'update now' control. Returns the job dict, or None if nothing is pending.

    **Maintenance is deliberately NOT lifted here.** It used to be, on the
    reasoning that a container replaced mid-call must not come back stuck in
    maintenance - but that opened the gate for the whole image-pull window, the
    one stretch where a new upload is most likely to be interrupted by the
    restart it just raced. The property is preserved from the other end
    instead: the new container clears maintenance on boot when nothing is
    pending (see main.py's lifespan), and the drain worker lifts it if the
    hand-off produces no new container within the stale window
    (audit 2026-07-30, flow-maintenance-5).

    On a hand-off FAILURE the pending record is restored so the next drain tick
    retries. That was not a rare path - the drain worker runs in the worker
    container, which had no /state bind mount, so this call failed EVERY time
    (audit 2026-07-30). The mount is fixed in docker-compose.yml.
    """
    pending = get_pending_update(db)
    if not pending:
        return None
    # Local import so this fix stays independent of the module import block,
    # which the hand-off fix also edits.
    import re

    tag = pending.get("target_tag")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        # The release-tag shape is enforced by a Pydantic validator on the admin
        # route, and this entry point never goes through it: the tag comes back
        # out of a kv row and straight into `docker pull ...:<tag>` on the host.
        # Anything that can write app_settings could otherwise pin the whole
        # deployment to a tag of its choosing, with no password re-auth. Drop the
        # record rather than hand it on, and rather than retry a value that will
        # never become valid once a minute forever.
        logger.error("pending update has an invalid target_tag=%r; discarding", tag)
        set_pending_update(db, None, actor=actor)
        # Nothing is going to happen, so the gate must not stay shut.
        set_enabled(db, False, actor=actor, audit=False)
        set_handoff_at(db, None, actor=actor)
        db.commit()
        return None
    set_pending_update(db, None, actor=actor)
    set_handoff_at(db, utc_now().isoformat(), actor=actor)
    db.commit()

    from .release_apply import apply as _release_apply
    try:
        result = _release_apply(action="update", target_tag=pending["target_tag"])
    except Exception:
        db.rollback()
        set_enabled(db, True, actor=actor, audit=False)
        set_pending_update(db, pending, actor=actor)
        set_handoff_at(db, None, actor=actor)
        db.commit()
        logger.exception(
            "pending update hand-off failed for tag=%s; restored maintenance + "
            "pending record so the next drain tick retries",
            pending["target_tag"],
        )
        raise
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
    # The direct /system/update path fans an ops alert out to every admin; the
    # postponed path did not, so the update an admin scheduled and walked away
    # from restarted the stack with nobody told. Same event, same audience,
    # regardless of which path fired it (audit 2026-07-30). Best-effort: a
    # notification failure must not unwind an update that has already been
    # handed to the executor.
    try:
        _dispatch_update_started_to_admins(db, tag=pending["target_tag"], via=reason)
    except Exception:
        logger.exception("pending update: admin ops alert failed (update proceeds)")
    logger.info("pending update applied: tag=%s via=%s", pending["target_tag"], reason)
    return result


def _dispatch_update_started_to_admins(db: Session, *, tag: str, via: str) -> None:
    """Same ops_alert fan-out the direct /system/update route performs.

    Lives here rather than in routers/admin/system.py because the postponed and
    forced paths run from the worker, which has no router to import from. The
    router keeps its own copy for the click-through path; both hit the same
    NotificationCategory.ops_alert, so an admin sees one consistent event
    whichever way the update was triggered."""
    from ..models.notification import NotificationCategory
    from ..models.user import UserRole
    from .notification import dispatch

    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    for a in admins:
        try:
            dispatch(
                db,
                user=a,
                category=NotificationCategory.ops_alert,
                payload={"reason": "update_triggered", "target_tag": tag, "via": via},
                link_url="/admin/system",
                email_to=a.email,
            )
        except Exception:
            logger.exception("update ops alert failed for admin=%s", a.id)


def cancel_pending_update(db: Session, *, actor: User | None, request=None) -> bool:
    """Cancel a postponed update and leave maintenance mode. Returns True if there
    was something to cancel."""
    had_pending = get_pending_update(db) is not None
    if not had_pending:
        # Nothing to cancel - and crucially, do NOT touch the maintenance flag.
        # This used to disable maintenance unconditionally, so an admin who had
        # enabled it by hand to run a database operation could have it silently
        # lifted by a colleague clicking Cancel on an update banner that was
        # already gone. Cancel only undoes what postpone did (audit 2026-07-30).
        return False
    set_pending_update(db, None, actor=actor)
    set_enabled(db, False, actor=actor)
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
