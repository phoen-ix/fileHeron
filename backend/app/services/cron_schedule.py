"""Admin-tunable cron schedules (v1.28.0).

Every background job's cadence is admin-editable: run every N minutes, or daily at
a fixed time (site timezone), or disabled. A single minute-dispatcher
(``workers/cron_dispatch.py``) reads this registry + the per-cron ``cron.<name>.*``
settings and enqueues jobs that are due. Defaults reproduce the historical fixed
ARQ schedule, so an upgrade is behaviour-neutral until an admin edits something.

This registry is also the allowlist for the on-demand "Run now" action (it replaced
the old ``_KNOWN_CRONS`` list).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from ..utils.timeutil import utc_now
from . import settings as settings_svc

KIND_INTERVAL = "interval"
KIND_DAILY = "daily"
_KINDS = (KIND_INTERVAL, KIND_DAILY)

_MAX_INTERVAL_MIN = 1440 * 7  # a week
_DEFAULT_DAILY = "02:00"


@dataclass(frozen=True)
class CronSpec:
    name: str
    group: str
    description: str
    default_kind: str
    default_interval_min: int = 60
    default_daily_time: str = _DEFAULT_DAILY
    min_interval_min: int = 1


# Order here is the display order. Defaults mirror the previous worker.py cadence.
REGISTRY: dict[str, CronSpec] = {
    s.name: s for s in [
        # Shares / files
        CronSpec("expire_files", "shares",
                 "Expire shares past their expiry and delete their files.", KIND_INTERVAL, 60),
        CronSpec("share_expiring_24h_warning", "shares",
                 "Warn recipients about shares expiring soon.", KIND_INTERVAL, 60),
        CronSpec("quota_reconcile", "shares",
                 "Reconcile per-user storage counters with the database.", KIND_INTERVAL, 60),
        CronSpec("cleanup_stale_uploads", "shares",
                 "Reap uploads stuck mid-transfer and fail empty shares.", KIND_INTERVAL, 60),
        CronSpec("cleanup_abandoned_uploads", "shares",
                 "Sweep abandoned TUS upload temp files.", KIND_INTERVAL, 60),
        CronSpec("purge_old_quarantine", "shares",
                 "Delete infected quarantined files past retention.", KIND_DAILY,
                 default_daily_time="02:13"),
        CronSpec("reclaim_orphaned_files", "shares",
                 "Free bytes + quota from long-revoked/deleted shares.", KIND_DAILY,
                 default_daily_time="02:51"),
        # Mail
        CronSpec("imap_poll", "mail",
                 "Fetch the inbound mailbox over IMAP.", KIND_INTERVAL, 5),
        CronSpec("rescan_inbound_attachments", "mail",
                 "Re-scan inbound attachments left unscanned (e.g. after a ClamAV outage).",
                 KIND_INTERVAL, 60),
        # Maintenance
        CronSpec("cleanup_expired_tokens", "maintenance",
                 "Revoke + delete expired auth tokens.", KIND_INTERVAL, 60),
        CronSpec("cleanup_pending_invites", "maintenance",
                 "Delete unused invites past retention.", KIND_DAILY,
                 default_daily_time="02:15"),
        CronSpec("cleanup_read_notifications", "maintenance",
                 "Delete old read notifications.", KIND_DAILY, default_daily_time="02:29"),
        CronSpec("prune_history", "maintenance",
                 "Prune audit/download/email/login/inbound history past retention.",
                 KIND_DAILY, default_daily_time="02:43"),
        # Ops / system
        CronSpec("ops_check", "ops",
                 "Scan recent cron + service health and alert admins.", KIND_INTERVAL, 60),
        CronSpec("disk_check", "ops",
                 "Monitor free disk space and flip the low-storage flag.", KIND_INTERVAL, 60),
        CronSpec("anomaly_check", "ops",
                 "Heuristic anomaly scan (mass-download / stuffing).", KIND_INTERVAL, 60),
        CronSpec("release_check", "ops",
                 "Poll for new file:Heron releases.", KIND_INTERVAL, 1440),
        CronSpec("drain_pending_update", "ops",
                 "Apply a postponed update once in-flight transfers drain.",
                 KIND_INTERVAL, 1),
        CronSpec("analytics_aggregate", "ops",
                 "Daily storage snapshot for the analytics trend.", KIND_DAILY,
                 default_daily_time="02:05"),
    ]
}


@dataclass(frozen=True)
class ResolvedSchedule:
    name: str
    group: str
    description: str
    enabled: bool
    kind: str
    interval_minutes: int
    daily_time: str
    # When true (and the error-alert feature is enabled), a failure of this job
    # emails admins via services/error_alert.py. Toggled per task on the
    # Scheduled tasks admin page; default off.
    alert_on_failure: bool = False


def _key(name: str, field: str) -> str:
    return f"cron.{name}.{field}"


def _clamp_interval(spec: CronSpec, value: int) -> int:
    return max(spec.min_interval_min, min(_MAX_INTERVAL_MIN, value))


def effective(db: Session, name: str) -> ResolvedSchedule:
    spec = REGISTRY[name]
    enabled = settings_svc.get_bool(db, _key(name, "enabled"), default=True)
    kind = settings_svc.get(db, _key(name, "kind")) or spec.default_kind
    if kind not in _KINDS:
        kind = spec.default_kind
    interval = _clamp_interval(
        spec, settings_svc.get_int(db, _key(name, "interval_minutes"), default=spec.default_interval_min)
    )
    daily_time = settings_svc.get(db, _key(name, "daily_time")) or spec.default_daily_time
    alert_on_failure = settings_svc.get_bool(db, _key(name, "alert_on_failure"), default=False)
    return ResolvedSchedule(
        name=name, group=spec.group, description=spec.description,
        enabled=enabled, kind=kind, interval_minutes=interval, daily_time=daily_time,
        alert_on_failure=alert_on_failure,
    )


def get_last_run(db: Session, name: str) -> datetime | None:
    raw = settings_svc.get(db, _key(name, "last_run_at"))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def mark_ran(db: Session, name: str, when: datetime | None = None) -> None:
    settings_svc.set_value(
        db, key=_key(name, "last_run_at"), value=(when or utc_now()).isoformat(), actor=None
    )


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = value.split(":", 1)
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        return 2, 0


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        # Same widening as services/site.py: a path-shaped key raises
        # ValueError, and here it would kill the whole cron dispatcher rather
        # than one job.
        return ZoneInfo("UTC")


def is_due(res: ResolvedSchedule, last_run_at: datetime | None, now_utc: datetime, tz_name: str) -> bool:
    """Pure due-ness check. ``last_run_at`` / ``now_utc`` are naive UTC. A None
    last_run_at means 'never seeded' -> not due (the dispatcher seeds first)."""
    if not res.enabled or last_run_at is None:
        return False
    if res.kind == KIND_INTERVAL:
        return (now_utc - last_run_at) >= timedelta(minutes=res.interval_minutes)
    # daily: due once we've passed today's HH:MM (site tz) and haven't run since.
    tz = _zone(tz_name)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    h, m = _parse_hhmm(res.daily_time)
    sched_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_local < sched_local:
        return False
    sched_utc = sched_local.astimezone(timezone.utc).replace(tzinfo=None)
    return last_run_at < sched_utc


def effective_cadence_minutes(db: Session, name: str) -> int:
    res = effective(db, name)
    return res.interval_minutes if res.kind == KIND_INTERVAL else 1440


def next_run_at(
    res: ResolvedSchedule, last_run_at: datetime | None, now_utc: datetime, tz_name: str
) -> datetime | None:
    """Best-effort next-run estimate for display (naive UTC)."""
    if not res.enabled:
        return None
    if res.kind == KIND_INTERVAL:
        base = last_run_at or now_utc
        return base + timedelta(minutes=res.interval_minutes)
    tz = _zone(tz_name)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    h, m = _parse_hhmm(res.daily_time)
    sched_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_local >= sched_local:
        sched_local = sched_local + timedelta(days=1)
    return sched_local.astimezone(timezone.utc).replace(tzinfo=None)
