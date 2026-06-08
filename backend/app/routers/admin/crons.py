"""/api/admin/crons - admin-tunable scheduled tasks (v1.28.0).

Lists every cron with its admin-editable schedule (interval / daily / disabled)
and live status, and lets an admin change the schedule. The on-demand "Run now"
stays on ``/api/admin/system/crons/{name}/run``. Cadence is enforced by the
minute dispatcher (workers/cron_dispatch.py) reading services/cron_schedule.py.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.cron_run import CronRun, CronRunStatus
from ...models.user import User
from ...schemas.cron_settings import (
    CronCounts,
    CronListResponse,
    CronScheduleItem,
    UpdateCronScheduleRequest,
)
from ...services import cron_schedule as cs
from ...services import settings as settings_svc
from ...services import site as site_svc
from ...services.audit import record_audit_event
from ...utils.timeutil import utc_now

router = APIRouter()


def _item(db: Session, name: str, tz: str, now) -> CronScheduleItem:
    spec = cs.REGISTRY[name]
    res = cs.effective(db, name)
    last_run = cs.get_last_run(db, name)
    last = (
        db.query(CronRun)
        .filter(CronRun.job_name == name)
        .order_by(CronRun.started_at.desc())
        .first()
    )
    cutoff = now - timedelta(hours=24)
    counts = dict(
        db.query(CronRun.status, func.count(CronRun.id))
        .filter(CronRun.job_name == name, CronRun.started_at >= cutoff)
        .group_by(CronRun.status)
        .all()
    )

    def _c(s: CronRunStatus) -> int:
        return int(counts.get(s, 0) or counts.get(s.value, 0) or 0)

    last_status = None
    if last is not None:
        last_status = last.status.value if isinstance(last.status, CronRunStatus) else last.status
    nxt = cs.next_run_at(res, last_run, now, tz)

    return CronScheduleItem(
        name=name, group=spec.group, description=spec.description,
        enabled=res.enabled, kind=res.kind, interval_minutes=res.interval_minutes,
        daily_time=res.daily_time, min_interval_minutes=spec.min_interval_min,
        alert_on_failure=res.alert_on_failure,
        last_run_at=(last.started_at.isoformat() if last and last.started_at else None),
        last_status=last_status,
        last_duration_ms=(last.duration_ms if last else None),
        last_error=(last.error_msg if last else None),
        next_run_at=(nxt.isoformat() if nxt else None),
        last_24h=CronCounts(
            success=_c(CronRunStatus.success),
            failure=_c(CronRunStatus.failure),
            running=_c(CronRunStatus.running),
        ),
    )


@router.get("/crons", response_model=CronListResponse)
def list_crons(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> CronListResponse:
    tz = site_svc.get_site_timezone(db)
    now = utc_now()
    return CronListResponse(
        items=[_item(db, name, tz, now) for name in cs.REGISTRY],
        site_timezone=tz,
        error_alerts_enabled=settings_svc.get_bool(
            db, settings_svc.Keys.ERROR_ALERT_ENABLED, default=False
        ),
    )


@router.put("/crons/{name}", response_model=CronScheduleItem)
def update_cron(
    name: str,
    payload: UpdateCronScheduleRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> CronScheduleItem:
    if name not in cs.REGISTRY:
        raise AppError(404, "CRON_UNKNOWN", "Unknown scheduled task.")
    spec = cs.REGISTRY[name]
    if payload.kind == "interval" and payload.interval_minutes < spec.min_interval_min:
        raise AppError(
            400, "INTERVAL_TOO_SMALL",
            f"Interval must be at least {spec.min_interval_min} minute(s).",
        )

    def k(field: str) -> str:
        return f"cron.{name}.{field}"

    en = "true" if payload.enabled else "false"
    settings_svc.set_value(db, key=k("enabled"), value=en, actor=admin, request=request)
    settings_svc.set_value(db, key=k("kind"), value=payload.kind, actor=admin, request=request)
    settings_svc.set_value(
        db, key=k("interval_minutes"), value=str(payload.interval_minutes),
        actor=admin, request=request,
    )
    settings_svc.set_value(
        db, key=k("daily_time"), value=payload.daily_time, actor=admin, request=request
    )
    settings_svc.set_value(
        db, key=k("alert_on_failure"),
        value="true" if payload.alert_on_failure else "false",
        actor=admin, request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.cron_schedule_changed,
        actor_user_id=admin.id,
        target_type="cron",
        target_id=name,
        metadata={"enabled": payload.enabled, "kind": payload.kind,
                  "interval_minutes": payload.interval_minutes, "daily_time": payload.daily_time,
                  "alert_on_failure": payload.alert_on_failure},
        request=request,
    )
    db.commit()
    return _item(db, name, site_svc.get_site_timezone(db), utc_now())
