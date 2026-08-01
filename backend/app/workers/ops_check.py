"""Hourly ops-health probe.

Closes the "no operator alerting" gap from the operational audit. Runs at
minute :15, after the other crons have had a chance to fire (and the
cron_tracker has recorded any of their failures). Checks:

- AV reachability (via av_scan.ping)
- Redis reachability (ping)
- Recent SMTP undeliverable count in the audit_log (last 1h)
- Crons that are failing repeatedly (cron_runs, last 3h)

For each unhealthy signal, dispatches an `ops_alert` notification to
every non-disabled admin. De-duplicated via Redis keys
`fh:ops:alert:<reason>` with a 1h TTL so we don't spam admins on
extended outages.

The cron_tracker decorator records this run too, so the System view
can show "ops_check last ran 23 minutes ago".
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func

from ..database import SessionLocal
from ..models.audit_log import AuditEventType, AuditLog
from ..models.cron_run import CronRun, CronRunStatus
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..services.cron_tracker import track_cron
from ..services.notification import dispatch
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.ops_check")

_DEDUP_TTL_SEC = 3600
_SMTP_FAILURE_LOOKBACK = timedelta(hours=1)
# Wide enough to catch a five-minute cron that has been failing for a while, and
# a threshold rather than "any failure" so a single transient does not train
# operators to ignore the alert. imap_poll on a real instance produced 12
# failures in an hour, which is the shape this is sized for.
_CRON_FAILURE_LOOKBACK = timedelta(hours=3)
_CRON_FAILURE_THRESHOLD = 3




def _dedup_seen(reason: str) -> bool:
    """Return True if we've already dispatched this reason in the dedup
    window. Best-effort: if Redis is down, return False (better noisy)."""
    try:
        redis = get_redis()
        key = f"fh:ops:alert:{reason}"
        if redis.exists(key):
            return True
        redis.set(key, "1", ex=_DEDUP_TTL_SEC)
        return False
    except Exception:
        return False


def _alert_admins(db, *, reason: str, detail: str) -> int:
    if _dedup_seen(reason):
        return 0
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    payload = {"reason": reason, "detail": detail[:500], "at": utc_now().isoformat()}
    n = 0
    for admin in admins:
        try:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/system",
                email_to=admin.email,
            )
            n += 1
        except Exception:
            logger.exception("ops_alert dispatch failed admin=%d", admin.id)

    from ..database import run_after_commit
    from ..services import webhook as webhook_svc
    emit_payload = {"target_type": "ops", "target_id": reason, "metadata": payload}
    run_after_commit(
        db, lambda: webhook_svc.emit(db, webhook_svc.OPS_ALERT_EVENT, emit_payload)
    )
    return n


def _check_av(db) -> str | None:
    """Return None if AV is healthy, an error string otherwise."""
    try:
        from ..config import settings
        if getattr(settings, "AV_SKIP", False):
            return None
        from ..services import av_scan
        if av_scan.ping():
            return None
        return "ClamAV ping returned false"
    except Exception as e:
        return f"ClamAV check raised: {e}"


def _check_redis() -> str | None:
    try:
        get_redis().ping()
        return None
    except Exception as e:
        return f"Redis ping raised: {e}"


def _check_failing_crons(db) -> str | None:
    """Crons that failed repeatedly and recently.

    This docstring used to say ops_check runs "after the other crons have had a
    chance to fire (and the cron_tracker has recorded any of their failures)",
    which reads as though those failures were consumed here. They were not:
    the only alert reasons were av/redis/smtp, and there was no
    consecutive-failure detection anywhere in the backend.

    So a cron could fail forever and the only way an operator learned of it was
    by opening /admin/scheduled-tasks unprompted. Observed on a live instance:
    `imap_poll` failed 12 times in an hour, was correctly recorded as failed
    both in `cron_runs` and in the error log, and alerted nobody (audit #2, L1).

    Threshold rather than "any failure": a single transient failure is what the
    retry and the next scheduled run are for, and alerting on it would train
    operators to ignore the alert."""
    cutoff = utc_now() - _CRON_FAILURE_LOOKBACK
    rows = (
        db.query(CronRun.job_name, func.count(CronRun.id))
        .filter(
            CronRun.status == CronRunStatus.failure,
            CronRun.started_at >= cutoff,
        )
        .group_by(CronRun.job_name)
        .having(func.count(CronRun.id) >= _CRON_FAILURE_THRESHOLD)
        .all()
    )
    if not rows:
        return None
    worst = sorted(rows, key=lambda r: -r[1])
    return "; ".join(
        f"{name} failed {n} times in the last "
        f"{int(_CRON_FAILURE_LOOKBACK.total_seconds() // 3600)}h"
        for name, n in worst
    )


def _check_smtp(db) -> str | None:
    cutoff = utc_now() - _SMTP_FAILURE_LOOKBACK
    n = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.email_undeliverable.value,
            AuditLog.created_at >= cutoff,
        )
        .count()
    )
    if n == 0:
        return None
    return f"{n} email_undeliverable audit event(s) in the last hour"


@track_cron("ops_check")
async def ops_check(_ctx) -> dict:
    db = SessionLocal()
    dispatched = 0
    summary = {"av": "ok", "redis": "ok", "smtp": "ok", "crons": "ok"}
    try:
        av_err = _check_av(db)
        if av_err:
            summary["av"] = av_err
            dispatched += _alert_admins(db, reason="av_unhealthy", detail=av_err)

        redis_err = _check_redis()
        if redis_err:
            summary["redis"] = redis_err
            dispatched += _alert_admins(db, reason="redis_unhealthy", detail=redis_err)

        smtp_err = _check_smtp(db)
        if smtp_err:
            summary["smtp"] = smtp_err
            dispatched += _alert_admins(db, reason="smtp_failing", detail=smtp_err)

        cron_err = _check_failing_crons(db)
        if cron_err:
            summary["crons"] = cron_err
            dispatched += _alert_admins(db, reason="cron_failing", detail=cron_err)

        db.commit()
        return {"dispatched": dispatched, **summary}
    finally:
        db.close()
