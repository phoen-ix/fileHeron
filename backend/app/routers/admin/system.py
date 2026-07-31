"""/api/admin/system - operator-facing health + cron history.

Closes the "no admin observability" gap from the operational audit.
Surfaces:

- Live liveness of DB / Redis / AV (same probes /api/health does, but
  scoped to the admin shell so unauth callers don't see internals).
- Last N runs per registered cron, with duration + status + result.
- Recent cron failures across all crons (for the "what blew up
  overnight?" view).
- Count of email_undeliverable audit events in the last 24h (the
  signal that ops_check picks up to alert admins).

Read-only. No mutating endpoints - the cron_tracker writes the rows.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...config import settings
from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType, AuditLog
from ...models.cron_run import CronRun, CronRunStatus
from ...models.user import User, UserRole
from ...redis_client import get_redis
from ...services import cron_schedule
from ...utils.timeutil import utc_now

router = APIRouter()

# Allowlist for the on-demand run endpoint = the cron schedule registry (single
# source of truth, v1.28.0). Each is in WorkerSettings.functions (enqueueable by
# name) and idempotent.
_KNOWN_CRONS = list(cron_schedule.REGISTRY)




def _live_checks(db: Session) -> dict:
    """Mirror /api/health's probes - DB, Redis, AV. Probes run fresh on every
    call; `checked_at` records when, so the UI can show 'checked <time>'."""
    out: dict = {"checked_at": utc_now().isoformat()}

    # DB - if we got this far the request session worked, so just label OK.
    out["db"] = {"status": "ok", "error": None}

    try:
        get_redis().ping()
        out["redis"] = {"status": "ok", "error": None}
    except Exception as e:
        out["redis"] = {"status": "down", "error": str(e)[:200]}

    if getattr(settings, "AV_SKIP", False):
        out["av"] = {"status": "skipped", "error": None}
    else:
        try:
            from ...services import av_scan
            if av_scan.ping():
                out["av"] = {"status": "ok", "error": None}
            else:
                out["av"] = {"status": "down", "error": "ping returned false"}
        except Exception as e:
            out["av"] = {"status": "down", "error": str(e)[:200]}

    return out


def _cron_row_dict(row: CronRun | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "job_name": row.job_name,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "status": row.status.value if isinstance(row.status, CronRunStatus) else row.status,
        "duration_ms": row.duration_ms,
        "result_summary": row.result_summary,
        "error_msg": row.error_msg,
    }


@router.get("/system/status")
def system_status(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """One-shot view for the admin /admin/system page."""
    crons = []
    for name in _KNOWN_CRONS:
        last = (
            db.query(CronRun)
            .filter(CronRun.job_name == name)
            .order_by(CronRun.started_at.desc())
            .first()
        )
        # Success / failure counts in the last 24h, for the at-a-glance number.
        cutoff = utc_now() - timedelta(hours=24)
        counts = dict(
            db.query(CronRun.status, func.count(CronRun.id))
            .filter(CronRun.job_name == name, CronRun.started_at >= cutoff)
            .group_by(CronRun.status)
            .all()
        )
        crons.append(
            {
                "job_name": name,
                "last_run": _cron_row_dict(last),
                "last_24h": {
                    "success": int(counts.get(CronRunStatus.success, 0) or counts.get("success", 0) or 0),
                    "failure": int(counts.get(CronRunStatus.failure, 0) or counts.get("failure", 0) or 0),
                    "running": int(counts.get(CronRunStatus.running, 0) or counts.get("running", 0) or 0),
                },
            }
        )

    recent_failures = [
        _cron_row_dict(r)
        for r in (
            db.query(CronRun)
            .filter(CronRun.status == CronRunStatus.failure)
            .order_by(CronRun.started_at.desc())
            .limit(10)
            .all()
        )
    ]

    cutoff_24h = utc_now() - timedelta(hours=24)
    email_undeliverable_24h = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.email_undeliverable.value,
            AuditLog.created_at >= cutoff_24h,
        )
        .count()
    )

    from ...services import release_check as release_check_svc
    from ...version import GIT_SHA, VERSION

    cached = release_check_svc.read_cached(db)
    latest = cached.get("latest_version")
    update_available = bool(latest) and latest != VERSION

    return {
        "live": _live_checks(db),
        "crons": crons,
        "recent_failures": recent_failures,
        "email_undeliverable_24h": email_undeliverable_24h,
        "version": {
            "running": VERSION,
            "sha": GIT_SHA,
            "running_release_url": release_check_svc.html_release_url_for_tag(db, VERSION),
            "latest": latest,
            "update_available": update_available,
            "last_check_at": cached.get("last_check_at"),
            "last_success_at": cached.get("last_success_at"),
            "last_check_error": cached.get("last_check_error"),
            "release_notes": cached.get("latest_body"),
            "release_url": cached.get("latest_url"),
            "release_published_at": cached.get("latest_published_at"),
        },
    }


@router.get("/system/stream")
async def system_stream(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Long-lived SSE for the /admin/system page. Reuses the existing
    `sse_token` minted at /api/notifications/stream-token (browser
    can't send Authorization headers from EventSource) and checks the
    bearer is admin server-side. Per-stream auth, no per-event auth.
    """
    from ...services import sse as sse_svc
    from ...services import sse_token as sse_token_svc

    if token:
        user_id = sse_token_svc.verify(token)
        user = (
            db.query(User)
            .filter(User.id == user_id, User.is_disabled.is_(False))
            .one_or_none()
        )
    elif authorization and authorization.lower().startswith("bearer "):
        from ...services.auth import resolve_user_from_access_token
        jwt_str = authorization.split(" ", 1)[1].strip()
        user = resolve_user_from_access_token(db, jwt_str, settings)
    else:
        raise AppError(401, "AUTH_REQUIRED", "Authentication required.")

    if user is None or user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Admin role required.")

    # The stream does no DB work after this point, so release the pooled connection
    # now instead of pinning it for the whole ~60s life of the response. A handful of
    # open /admin/system tabs would otherwise hold a real slice of a 10+20 pool and
    # every other request would queue behind them until DB_POOL_TIMEOUT_SEC. get_db's
    # teardown close() then becomes a no-op. Mirrors routers/notifications.py::stream.
    db.close()

    # Same per-user cap the notification stream carries, on its own budget:
    # this route was the one long-lived connection an admin could open without
    # limit, each holding a Redis pubsub subscription for 60s.
    if not sse_svc.try_acquire_admin_stream(user.id):
        raise AppError(
            429, "TOO_MANY_STREAMS", "Too many concurrent connections; close some tabs."
        )

    admin_id = user.id

    async def _capped_stream():
        try:
            async for frame in sse_svc.stream_admin_events():
                yield frame
        finally:
            sse_svc.release_admin_stream(admin_id)

    return StreamingResponse(
        _capped_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/system/check-updates")
async def check_updates_now(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """Admin-triggered release check. Bypasses both the manual-mode guard
    and the 24h cadence - fires a real HTTP call and writes the cache.
    Returns the same shape as the cron's result so the SPA can toast
    'found vX.Y.Z' / 'up to date' / error."""
    from ...services import release_check as release_check_svc
    return await release_check_svc.run_check(db, manual=True)


@router.get("/system/cron-runs")
def cron_runs(
    job_name: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """Paginated cron-runs feed for digging into a specific job's history."""
    q = db.query(CronRun)
    if job_name:
        q = q.filter(CronRun.job_name == job_name)
    rows = q.order_by(CronRun.started_at.desc()).limit(limit).all()
    return {"items": [_cron_row_dict(r) for r in rows], "limit": limit}


@router.get("/system/live")
def live_checks_now(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """On-demand re-run of the liveness probes (DB / Redis / AV). Lightweight
    sibling of /system/status that skips the cron + version queries - backs
    the 'Re-run' button on the Live checks card."""
    return {"live": _live_checks(db)}


@router.post("/system/crons/{job_name}/run")
async def run_cron_now(
    job_name: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Enqueue a scheduled cron to run immediately on the worker. The job's
    own @track_cron wrapper writes the CronRun row + publishes the SSE
    'cron_run' event, so the status table updates like any scheduled run.

    Restricted to the known-cron allowlist so arbitrary worker functions
    (which may require arguments) can't be enqueued through here."""
    if job_name not in _KNOWN_CRONS:
        raise AppError(404, "CRON_UNKNOWN", f"Unknown cron job '{job_name}'.")

    from ...services import job_queue
    from ...services.audit import record_audit_event

    await job_queue.aenqueue(job_name)
    record_audit_event(
        db,
        event_type=AuditEventType.cron_run_triggered,
        actor_user_id=admin.id,
        target_type="cron",
        target_id=job_name,
        metadata={"reason": "admin_on_demand"},
        request=request,
    )
    db.commit()
    return {"job_name": job_name, "queued": True}


# ---------------------------------------------------------------------------
# Self-update endpoints (v1.0.0 architecture). Backend writes update
# requests to /state/current_job.json; the shim container polls that
# file and spawns the executor. Trust is filesystem-membership in the
# compose project; no HMAC/HTTP between backend and shim. The user-
# facing chain (admin auth + password re-prompt + audit + notify-all-
# admins) stays at this boundary, unchanged.
# ---------------------------------------------------------------------------


class UpdateApplyRequest(BaseModel):
    # Same password the admin used to log in. Required to defend against
    # session-hijack abuse of a destructive-by-design action.
    password: str = Field(..., min_length=1, max_length=512)
    # Tag to apply. Updates only; rollback ignores this and reads the
    # stored rollback_target from the updater's state file.
    target_tag: str | None = Field(default=None, max_length=64)
    # When true, don't apply immediately: enter maintenance mode and let the
    # drain worker apply once in-flight transfers finish (or the max-wait cap
    # elapses). The SPA sets this after seeing active transfers.
    postpone: bool = Field(default=False)

    @field_validator("target_tag")
    @classmethod
    def _validate_target_tag(cls, v: str | None) -> str | None:
        # The tag flows into `docker pull ghcr.io/.../*:<tag>` and the FH_TAG
        # env on the host, so constrain it to the exact release-tag shape - no
        # shell metacharacters, no `latest`, no arbitrary ref (audit L22/L25).
        # The pattern is release_check's own constant: while the two were
        # written separately, release-check surfaced suffixed tags this
        # rejected, so the update banner offered a version whose button 422'd
        # (audit 2026-07-30).
        if v is None:
            return v
        from ...services.release_check import RELEASE_TAG_RE
        if not RELEASE_TAG_RE.fullmatch(v):
            raise ValueError("target_tag must be a release tag like v1.2.3")
        return v


def _verify_password_or_403(user: User, password: str) -> None:
    from ...utils.crypto import argon2_verify
    # 403 (not 401) on a wrong confirm-password: the admin IS authenticated -
    # this is a re-auth gate, not a session failure. A 401 here collides with
    # the SPA's global access-token-refresh interceptor, which would silently
    # refresh the session and re-submit the update with the same wrong
    # password, masking the error (the user saw "nothing happened"). A
    # distinct INVALID_PASSWORD code lets the UI show a precise message.
    if not argon2_verify(user.password_hash, password):
        raise AppError(403, "INVALID_PASSWORD", "Password incorrect.")


def _dispatch_ops_to_admins(db: Session, payload: dict, link_url: str) -> None:
    """Fan out an `ops_alert` notification to every non-disabled admin so
    every admin sees the in-app bell + email about a triggered update,
    not just the admin who clicked. Mirrors cron_tracker._maybe_alert_admins."""
    from ...models.notification import NotificationCategory
    from ...services.notification import dispatch
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
                payload=payload,
                link_url=link_url,
                email_to=a.email,
            )
        except Exception:
            # Notification dispatch must never block the update.
            pass


@router.get("/system/update-status")
def update_status(_admin: User = Depends(get_current_admin)) -> dict:
    """Read-only: what's the updater's current state? Returns
    {current_tag, rollback_target, job_in_progress}. Frontend polls
    this on mount + after kicking off a job."""
    from ...services import release_apply
    return release_apply.get_version()


@router.get("/system/update-jobs/{job_id}")
def update_job(
    job_id: str, _admin: User = Depends(get_current_admin)
) -> dict:
    """Poll a specific job. The SPA hits this on a short interval while
    `state` is one of {queued, pulling, restarting}."""
    from ...services import release_apply
    return release_apply.get_job(job_id)


@router.post("/system/update")
def apply_update(
    payload: UpdateApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Kick off an update to `target_tag`. Returns {job_id} immediately;
    the SPA polls /system/update-jobs/{job_id} for state."""
    from ...services import release_apply
    from ...services.audit import record_audit_event

    _verify_password_or_403(admin, payload.password)
    if not payload.target_tag:
        raise AppError(400, "INVALID_INPUT", "target_tag is required.")

    if payload.postpone:
        from datetime import timedelta

        from ...services import maintenance as maintenance_svc
        from ...services import settings_registry
        wait_min = settings_registry.effective(
            db, settings_registry.K.UPDATES_DRAIN_MAX_WAIT_MIN
        )
        deadline = (utc_now() + timedelta(minutes=int(wait_min))).isoformat()
        maintenance_svc.set_enabled(db, True, actor=admin, request=request)
        maintenance_svc.set_pending_update(
            db,
            {
                "target_tag": payload.target_tag,
                "deadline_iso": deadline,
                "requested_by_id": admin.id,
            },
            actor=admin,
        )
        record_audit_event(
            db,
            event_type=AuditEventType.update_postponed,
            actor_user_id=admin.id,
            target_type="update_job",
            target_id=None,
            metadata={"target_tag": payload.target_tag, "deadline": deadline},
            request=request,
        )
        db.commit()
        return {
            "postponed": True,
            "target_tag": payload.target_tag,
            "deadline_iso": deadline,
        }

    # A direct (non-postponed) update must not leave an earlier POSTPONED record
    # behind: drain_pending_update would otherwise re-fire it and the box could
    # boot stuck in maintenance mode. Clear both idempotently (no-op when none).
    from ...services import maintenance as maintenance_svc
    if maintenance_svc.get_pending_update(db) is not None:
        maintenance_svc.set_pending_update(db, None, actor=admin)
        maintenance_svc.set_enabled(db, False, actor=admin, request=request)
        db.commit()

    result = release_apply.apply(action="update", target_tag=payload.target_tag)

    record_audit_event(
        db,
        event_type=AuditEventType.update_triggered,
        actor_user_id=admin.id,
        target_type="update_job",
        target_id=result["job_id"],
        metadata={"target_tag": payload.target_tag},
        request=request,
    )
    _dispatch_ops_to_admins(
        db,
        payload={
            "reason": "update_triggered",
            "actor_id": admin.id,
            "target_tag": payload.target_tag,
            "job_id": result["job_id"],
        },
        link_url="/admin/system",
    )
    db.commit()
    return result


@router.post("/system/rollback")
def apply_rollback(
    payload: UpdateApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Roll back to the previously-running tag. Target is read from the
    updater's state file - caller doesn't pass it."""
    from ...services import release_apply
    from ...services.audit import record_audit_event

    _verify_password_or_403(admin, payload.password)
    result = release_apply.apply(action="rollback", target_tag=None)

    record_audit_event(
        db,
        event_type=AuditEventType.rollback_triggered,
        actor_user_id=admin.id,
        target_type="update_job",
        target_id=result["job_id"],
        metadata={"target_tag": result.get("target_tag")},
        request=request,
    )
    _dispatch_ops_to_admins(
        db,
        payload={
            "reason": "rollback_triggered",
            "actor_id": admin.id,
            "target_tag": result.get("target_tag"),
            "job_id": result["job_id"],
        },
        link_url="/admin/system",
    )
    db.commit()
    return result


@router.get("/system/transfer-activity")
def transfer_activity(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> dict:
    """Live in-flight uploads/downloads + any postponed-update record. Drives the
    Update dialog ('N transfers in progress') and the postponed banner."""
    from ...services import maintenance as maintenance_svc
    from ...services import transfer_activity as ta

    snap = ta.snapshot(db)
    snap["maintenance_enabled"] = maintenance_svc.is_enabled(db)
    snap["pending_update"] = maintenance_svc.get_pending_update(db)
    return snap


@router.post("/system/update/now")
def force_pending_update(
    payload: UpdateApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Apply a postponed update immediately, without waiting for transfers to
    drain. Password re-auth, same as the initial Update."""
    from ...services import maintenance as maintenance_svc

    _verify_password_or_403(admin, payload.password)
    result = maintenance_svc.apply_pending_update(
        db, actor=admin, request=request, reason="admin_force"
    )
    if result is None:
        raise AppError(409, "NO_PENDING_UPDATE", "There is no postponed update to apply.")
    return result


@router.post("/system/update/cancel")
def cancel_pending_update(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Cancel a postponed update and leave maintenance mode."""
    from ...services import maintenance as maintenance_svc

    cancelled = maintenance_svc.cancel_pending_update(db, actor=admin, request=request)
    return {"cancelled": cancelled}
