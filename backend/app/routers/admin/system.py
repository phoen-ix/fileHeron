"""/api/admin/system — operator-facing health + cron history.

Closes the "no admin observability" gap from the operational audit.
Surfaces:

- Live liveness of DB / Redis / AV (same probes /api/health does, but
  scoped to the admin shell so unauth callers don't see internals).
- Last N runs per registered cron, with duration + status + result.
- Recent cron failures across all crons (for the "what blew up
  overnight?" view).
- Count of email_undeliverable audit events in the last 24h (the
  signal that ops_check picks up to alert admins).

Read-only. No mutating endpoints — the cron_tracker writes the rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...config import settings
from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType, AuditLog
from ...models.cron_run import CronRun, CronRunStatus
from ...models.user import User, UserRole
from ...redis_client import get_redis

router = APIRouter()

_KNOWN_CRONS = [
    "expire_files",
    "share_expiring_24h_warning",
    "cleanup_expired_tokens",
    "quota_reconcile",
    "ops_check",
    "release_check",
]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _live_checks(db: Session) -> dict:
    """Mirror /api/health's probes — DB, Redis, AV."""
    out: dict[str, dict[str, str | None]] = {}

    # DB — if we got this far the request session worked, so just label OK.
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
        cutoff = _utcnow() - timedelta(hours=24)
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

    cutoff_24h = _utcnow() - timedelta(hours=24)
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
            "latest": latest,
            "update_available": update_available,
            "last_check_at": cached.get("last_check_at"),
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

    return StreamingResponse(
        sse_svc.stream_admin_events(),
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
    and the 24h cadence — fires a real HTTP call and writes the cache.
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


# ---------------------------------------------------------------------------
# Self-update endpoints (v1.0.0 architecture). Backend writes update
# requests to /state/current_job.json; the shim container polls that
# file and spawns the executor. Trust is filesystem-membership in the
# compose project; no HMAC/HTTP between backend and shim. The user-
# facing chain (admin auth + password re-prompt + audit + notify-all-
# admins) stays at this boundary, unchanged.
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field


class UpdateApplyRequest(BaseModel):
    # Same password the admin used to log in. Required to defend against
    # session-hijack abuse of a destructive-by-design action.
    password: str = Field(..., min_length=1, max_length=512)
    # Tag to apply. Updates only; rollback ignores this and reads the
    # stored rollback_target from the updater's state file.
    target_tag: str | None = Field(default=None, max_length=64)


def _verify_password_or_401(user: User, password: str) -> None:
    from ...utils.crypto import argon2_verify
    if not argon2_verify(user.password_hash, password):
        raise AppError(401, "INVALID_CREDENTIALS", "Password incorrect.")


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

    _verify_password_or_401(admin, payload.password)
    if not payload.target_tag:
        raise AppError(400, "INVALID_INPUT", "target_tag is required.")

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
    updater's state file — caller doesn't pass it."""
    from ...services import release_apply
    from ...services.audit import record_audit_event

    _verify_password_or_401(admin, payload.password)
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
