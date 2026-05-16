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

    from ...version import GIT_SHA, VERSION

    return {
        "live": _live_checks(db),
        "crons": crons,
        "recent_failures": recent_failures,
        "email_undeliverable_24h": email_undeliverable_24h,
        "version": {"running": VERSION, "sha": GIT_SHA},
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
