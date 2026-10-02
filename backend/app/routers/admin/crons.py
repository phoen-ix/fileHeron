"""/api/admin/crons - admin-tunable scheduled tasks (v1.28.0).

Lists every cron with its admin-editable schedule (interval / daily / disabled)
and live status, and lets an admin change the schedule. The on-demand "Run now"
stays on ``/api/admin/system/crons/{name}/run``. Cadence is enforced by the
minute dispatcher (workers/cron_dispatch.py) reading services/cron_schedule.py.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.cron_run import CronRunStatus
from ...models.user import User
from ...schemas.cron_settings import (
    CronCounts,
    CronListResponse,
    CronScheduleItem,
    UpdateCronScheduleRequest,
)
from ...services import cron_schedule as cs
from ...services import cron_tracker
from ...services import settings as settings_svc
from ...services import site as site_svc
from ...services.audit import record_audit_event
from ...utils.timeutil import utc_now

router = APIRouter()


def _items(db: Session, names, tz: str, now) -> list[CronScheduleItem]:
    """The rows for `names`, in a fixed number of queries: one settings read for
    every schedule (cron_schedule.snapshot), one for the newest run of every
    task, one grouped count. Built per task this was about nine queries each."""
    snap = cs.snapshot(db)
    latest = cron_tracker.latest_runs(db)
    counts = cron_tracker.run_counts_since(db, now - timedelta(hours=24))
    out: list[CronScheduleItem] = []
    for name in names:
        spec = cs.REGISTRY[name]
        res = snap.schedules[name]
        last = latest.get(name)
        c = counts.get(name, {})
        last_status = None
        if last is not None:
            last_status = (
                last.status.value if isinstance(last.status, CronRunStatus) else last.status
            )
        nxt = cs.next_run_at(res, snap.last_runs[name], now, tz)
        out.append(CronScheduleItem(
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
                success=c.get("success", 0),
                failure=c.get("failure", 0),
                running=c.get("running", 0),
            ),
        ))
    return out


@router.get("/crons", response_model=CronListResponse)
def list_crons(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> CronListResponse:
    tz = site_svc.get_site_timezone(db)
    now = utc_now()
    return CronListResponse(
        items=_items(db, list(cs.REGISTRY), tz, now),
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
    return _items(db, [name], site_svc.get_site_timezone(db), utc_now())[0]
