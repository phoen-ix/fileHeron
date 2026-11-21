"""One fan-out for "tell every admin".

Six places each queried the enabled admins, looped, dispatched and swallowed a
per-admin failure in their own way - the ops alerts the crons raise, the
update-triggered notice (twice: router and worker), the inbound-mail notice.
Four more were found on 2026-11-08 (the server-error alert, the scan-guard
block notice, release-available, the quarantine notice); an AST test now fails
any loop over admins that dispatches outside this module.
Only some of them counted what failed, and none reported it: a cron whose
alert could not be delivered still returned a success.

`notify_admins` is that loop, once. It never raises - a missing notification
must not fail the action that produced it (services/notification.py) - but it
RETURNS what happened, so a cron can fold a failed fan-out into its own result
(`cron_tracker.mark_failed`) and the Scheduled-tasks page shows it.

`OPS_REASONS` names every reason an `ops_alert` is sent for. The bell words
each one from it (`notif_bell.headline.ops_alert.<reason>`); ten of them read
"Operational alert." and nothing else until 2026-11-21, because nothing said
which reasons existed. `tests/test_ops_alert_reasons.py` reads both sides.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..utils.timeutil import utc_now
from . import alert_dedup
from . import notification as notif_svc

logger = logging.getLogger("fileheron.admin_alerts")

# Every `payload["reason"]` an ops alert carries.
OPS_REASONS: frozenset[str] = frozenset(
    {
        # Antivirus
        "av_scan_failed",  # a scan ended without a verdict (workers/av_scan)
        "av_unhealthy",  # clamd does not answer (ops_check)
        # Storage
        "storage_critical_low",
        "storage_unreadable",
        # Scheduled tasks
        "cron_failed",  # one run failed (cron_tracker)
        "cron_failing",  # a task keeps failing (ops_check)
        # Services
        "redis_unhealthy",
        "smtp_failing",
        # Security
        "anomaly",
        "scan_guard_block",
        # Updates
        "update_auto_scheduled",
        "update_triggered",
        "rollback_triggered",
        "update_completed",
        "update_failed",
        # Housekeeping
        "orphaned_files_reclaimed",
    }
)

# The test suite turns this on (tests/conftest.py): an unregistered reason then
# raises, so every test that sends an ops alert is a registry test - the way
# conftest's flush listener makes every test a column-width test. In production
# an unknown reason is still delivered: an alarm is never dropped over a
# missing registry line.
STRICT_REASONS = False

_OPS_DEDUP_TTL_SEC = 3600


@dataclass(frozen=True)
class FanOut:
    """What one fan-out did: how many admins got the notification, how many
    dispatches raised, and the first error's one-line summary."""

    sent: int = 0
    failed: int = 0
    first_error: str | None = None


def enabled_admins(db: Session) -> list[User]:
    return (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )


def notify_admins(
    db: Session,
    *,
    payload: dict[str, Any],
    link_url: str | None = "/admin/system",
    category: NotificationCategory = NotificationCategory.ops_alert,
    email_for: Callable[[User], str | None] | None = None,
    exclude_user_id: int | None = None,
) -> FanOut:
    """Dispatch one notification to every enabled admin. Caller commits.

    An `ops_alert` payload gets an `at` stamp when it has none - the mail
    renders it as the time, and without it an alert carried no time at all.
    `email_for(admin)` picks the address per admin (None = bell only); the
    default is the admin's own. `exclude_user_id` skips one admin (the
    quarantine notice must not reach an uploader twice). A dispatch that raises
    is logged and counted, never propagated."""
    if category == NotificationCategory.ops_alert:
        reason = payload.get("reason")
        if reason not in OPS_REASONS:
            if STRICT_REASONS:
                raise ValueError(f"ops alert reason {reason!r} is not in admin_alerts.OPS_REASONS")
            logger.warning("ops alert with an unregistered reason: %r", reason)
        if not payload.get("at"):
            payload = {**payload, "at": utc_now().isoformat()}
    pick_email = email_for or (lambda admin: admin.email)
    sent = failed = 0
    first_error: str | None = None
    for admin in enabled_admins(db):
        if admin.id == exclude_user_id:
            continue
        try:
            notif_svc.dispatch(
                db,
                user=admin,
                category=category,
                payload=payload,
                link_url=link_url,
                email_to=pick_email(admin),
            )
            sent += 1
        except Exception as exc:
            failed += 1
            first_error = first_error or f"{type(exc).__name__}: {exc}"
            logger.exception(
                "%s dispatch to admin=%d failed", category.value, admin.id
            )
    return FanOut(sent=sent, failed=failed, first_error=first_error)


def ops_alert(
    db: Session,
    *,
    reason: str,
    detail: str,
    link_url: str | None = "/admin/system",
    dedup: bool = True,
) -> FanOut:
    """One operational alert to every admin, plus the ops webhook. Caller commits.

    `dedup` (default): at most one per reason per hour (services/alert_dedup),
    so a condition that persists - or hits a hundred files at once - alarms
    once an hour, not once per occurrence. A caller with its own dedup passes
    False."""
    if dedup and alert_dedup.seen_recently(f"fh:ops:alert:{reason}", _OPS_DEDUP_TTL_SEC):
        return FanOut()
    payload = {"reason": reason, "detail": detail[:500], "at": utc_now().isoformat()}
    out = notify_admins(db, payload=payload, link_url=link_url)

    from . import webhook as webhook_svc

    webhook_svc.emit_after_commit(
        db,
        webhook_svc.OPS_ALERT_EVENT,
        {"target_type": "ops", "target_id": reason, "metadata": payload},
    )
    return out
