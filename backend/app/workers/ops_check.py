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

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func

from ..database import SessionLocal
from ..models.audit_log import AuditEventType, AuditLog
from ..models.cron_run import CronRun, CronRunStatus
from ..redis_client import get_redis
from ..services import admin_alerts, alert_dedup
from ..services.cron_tracker import mark_failed, track_cron
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
    """True if we've already dispatched this reason in the dedup window. With
    Redis down the in-process record answers, so `redis_unhealthy` itself goes
    out once per window rather than on every run."""
    return alert_dedup.seen_recently(f"fh:ops:alert:{reason}", _DEDUP_TTL_SEC)


def _alert_admins(db, *, reason: str, detail: str) -> admin_alerts.FanOut:
    if _dedup_seen(reason):
        return admin_alerts.FanOut()
    payload = {"reason": reason, "detail": detail[:500], "at": utc_now().isoformat()}
    out = admin_alerts.notify_admins(db, payload=payload, link_url="/admin/system")

    from ..services import webhook as webhook_svc
    emit_payload = {"target_type": "ops", "target_id": reason, "metadata": payload}
    webhook_svc.emit_after_commit(db, webhook_svc.OPS_ALERT_EVENT, emit_payload)
    return out


# One failed ping is not an outage. clamd stops answering for several seconds
# while it activates a freshly downloaded signature database - freshclam does
# that once a day, at a time anchored to when the clamav container started -
# and the hourly check landed in that window on 2026-10-01 and -02, mailing
# every admin "av_unhealthy" about a daemon that never went down. A real outage
# outlasts the retries; a reload does not. Worst case 3 x the 5 s ping timeout
# plus 2 x 10 s, well inside the job's timeout.
_AV_PING_ATTEMPTS = 3
_AV_PING_RETRY_DELAY_SEC = 10.0


async def _check_av() -> str | None:
    """Return None if AV is healthy, an error string otherwise. The ping runs in
    a thread (it is blocking socket I/O) and the waits are asyncio sleeps, so a
    real outage does not freeze the worker's event loop for half a minute."""
    try:
        from ..config import settings
        if getattr(settings, "AV_SKIP", False):
            return None
        from ..services import av_scan
        for attempt in range(_AV_PING_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_AV_PING_RETRY_DELAY_SEC)
            if await asyncio.to_thread(av_scan.ping):
                if attempt:
                    logger.info("ops_check: ClamAV answered on attempt %d", attempt + 1)
                return None
        return (
            f"ClamAV ping returned false {_AV_PING_ATTEMPTS} times over "
            f"{int(_AV_PING_RETRY_DELAY_SEC * (_AV_PING_ATTEMPTS - 1))} s"
        )
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
    dispatched = failed = 0
    first_error: str | None = None
    summary = {"av": "ok", "redis": "ok", "smtp": "ok", "crons": "ok"}
    try:
        checks = (
            ("av", "av_unhealthy", await _check_av()),
            ("redis", "redis_unhealthy", _check_redis()),
            ("smtp", "smtp_failing", _check_smtp(db)),
            ("crons", "cron_failing", _check_failing_crons(db)),
        )
        for key, reason, err in checks:
            if not err:
                continue
            summary[key] = err
            fan = _alert_admins(db, reason=reason, detail=err)
            dispatched += fan.sent
            failed += fan.failed
            first_error = first_error or fan.first_error

        db.commit()
        out = {"dispatched": dispatched, **summary}
        if failed:
            # The alerts ARE this job's work: one that could not be delivered is
            # a failed run, not a quiet one.
            return mark_failed(out, f"{failed} alert dispatch(es) failed: {first_error}")
        return out
    finally:
        db.close()
