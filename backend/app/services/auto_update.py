"""Automatic updates (off by default).

When an admin switches this on, the daily `auto_update` cron installs a newer
release by itself. It never applies anything directly: it schedules the update
exactly as "Postpone" does - maintenance on plus a `maintenance.pending_update`
record - and the minute `drain_pending_update` worker applies it once transfers
drain or `updates.drain_max_wait_min` runs out. So an automatic update takes the
same path, the same pre-update backup (`updates.backup_default`) and the same
alerts as a postponed one, and `maintenance.report_handoff_outcome` announces
how it ended.

Turning it on, or changing it while it is on, is password-gated
(`PUT /api/admin/settings/auto-update`): an automatic update skips the password
every manual update asks for, and a setting that removes a step-up gate must not
be reachable without one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..models.audit_log import AuditEventType
from ..utils.timeutil import to_naive_utc, utc_now
from . import settings as settings_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.auto_update")

# patch: same major.minor (v2.19.1 -> v2.19.2); minor: same major; any: also
# a new major. Minor releases can carry infra majors (v2.19.0 moved MariaDB
# 11 -> 12.3), which is why the default stays at patch.
SCOPES = ("patch", "minor", "any")
MIN_AGE_HOURS_MAX = 720
# The release cache is refreshed daily. Older than this, "the newest release"
# is whatever the last successful check saw days ago - nothing is installed on it.
CACHE_MAX_AGE_HOURS = 48
CRON_NAME = "auto_update"


@dataclass(frozen=True)
class AutoUpdateSettings:
    enabled: bool
    scope: str
    min_age_hours: int


def get_settings(db: Session) -> AutoUpdateSettings:
    keys = settings_svc.Keys
    scope = settings_svc.get(db, keys.UPDATES_AUTO_SCOPE) or app_settings.UPDATES_AUTO_SCOPE
    age = settings_svc.get_int(db, keys.UPDATES_AUTO_MIN_AGE_HOURS, app_settings.UPDATES_AUTO_MIN_AGE_HOURS)
    return AutoUpdateSettings(
        enabled=settings_svc.get_bool(db, keys.UPDATES_AUTO_ENABLED, app_settings.UPDATES_AUTO_ENABLED),
        scope=scope if scope in SCOPES else "patch",
        min_age_hours=max(0, min(MIN_AGE_HOURS_MAX, age)),
    )


def skipped_tag(db: Session) -> str | None:
    return settings_svc.get(db, settings_svc.Keys.UPDATES_AUTO_SKIP_TAG) or None


def _parse_utc(value: str | None) -> datetime | None:
    """The cache holds both GitHub's `...Z` and our own naive UTC stamps."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return to_naive_utc(parsed) if parsed.tzinfo is not None else parsed


def eligible_target(db: Session, settings: AutoUpdateSettings) -> tuple[str | None, str]:
    """The release the automatic updater would install now, or None with the
    reason it would not."""
    from .. import version as version_mod
    from .release_check import CacheKeys, is_newer, version_key

    running = version_mod.VERSION
    running_key = version_key(running)
    if running_key is None:
        # A dev build has no version to compare scope against, and a source
        # checkout silently becoming a release image is not an "update".
        return None, "not_a_release_build"
    latest = settings_svc.get(db, CacheKeys.LATEST_VERSION)
    if not is_newer(latest, running):
        return None, "up_to_date"
    latest_key = version_key(latest)
    assert latest_key is not None  # is_newer against a release build implies it
    if settings.scope == "patch" and latest_key[:2] != running_key[:2]:
        return None, "out_of_scope"
    if settings.scope == "minor" and latest_key[0] != running_key[0]:
        return None, "out_of_scope"
    if latest == skipped_tag(db):
        return None, "skipped_after_failure"
    now = utc_now()
    checked = _parse_utc(settings_svc.get(db, CacheKeys.LAST_SUCCESS_AT))
    if checked is None or now - checked > timedelta(hours=CACHE_MAX_AGE_HOURS):
        return None, "release_check_stale"
    published = _parse_utc(settings_svc.get(db, CacheKeys.LATEST_PUBLISHED_AT))
    if published is None:
        return None, "publication_time_unknown"
    if now - published < timedelta(hours=settings.min_age_hours):
        return None, "too_new"
    return latest, "eligible"


def schedule(db: Session) -> dict:
    """What the daily cron does: schedule the eligible release, or say why not.
    Commits. Never applies anything itself."""
    from . import maintenance, release_apply, settings_registry

    settings = get_settings(db)
    if not settings.enabled:
        return {"scheduled": False, "reason": "disabled"}
    tag, reason = eligible_target(db, settings)
    if tag is None:
        return {"scheduled": False, "reason": reason}
    if release_apply.get_version().get("job_in_progress"):
        return {"scheduled": False, "reason": "job_in_progress", "target_tag": tag}
    if maintenance.get_pending_update(db) is not None:
        return {"scheduled": False, "reason": "update_already_pending", "target_tag": tag}
    if maintenance.is_enabled(db):
        # Maintenance an operator turned on is theirs; an update riding on it
        # would lift it when the new container boots.
        return {"scheduled": False, "reason": "maintenance_on", "target_tag": tag}
    backup = bool(settings_registry.effective(db, settings_registry.K.UPDATES_BACKUP_DEFAULT))
    deadline = maintenance.schedule_pending_update(
        db, target_tag=tag, backup=backup, requested_by=None, origin="auto",
    )
    record_audit_event(
        db,
        event_type=AuditEventType.update_auto_scheduled,
        actor_user_id=None,
        target_type="update_job",
        target_id=None,
        metadata={"target_tag": tag, "deadline": deadline, "backup": backup,
                  "scope": settings.scope, "min_age_hours": settings.min_age_hours},
    )
    db.commit()
    try:
        maintenance.notify_admins(
            db,
            payload={
                "reason": "update_auto_scheduled",
                "target_tag": tag,
                "via": "auto",
                "detail": (f"Automatic update to {tag}: new transfers are paused and it "
                           f"starts once running ones finish, at the latest {deadline[:16]} UTC."),
            },
        )
        db.commit()
    except Exception:
        logger.exception("automatic update: admin ops alert failed (update stays scheduled)")
    logger.info("automatic update scheduled: %s (deadline %s)", tag, deadline)
    return {"scheduled": True, "target_tag": tag, "deadline_iso": deadline}
