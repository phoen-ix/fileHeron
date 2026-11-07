"""One fan-out for "tell every admin".

Six places each queried the enabled admins, looped, dispatched and swallowed a
per-admin failure in their own way - the ops alerts the crons raise, the
update-triggered notice (twice: router and worker), the inbound-mail notice.
Only some of them counted what failed, and none reported it: a cron whose
alert could not be delivered still returned a success.

`notify_admins` is that loop, once. It never raises - a missing notification
must not fail the action that produced it (services/notification.py) - but it
RETURNS what happened, so a cron can fold a failed fan-out into its own result
(`cron_tracker.mark_failed`) and the Scheduled-tasks page shows it.
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
from . import notification as notif_svc

logger = logging.getLogger("fileheron.admin_alerts")


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
    link_url: str = "/admin/system",
    category: NotificationCategory = NotificationCategory.ops_alert,
    email_for: Callable[[User], str | None] | None = None,
) -> FanOut:
    """Dispatch one notification to every enabled admin. Caller commits.

    An `ops_alert` payload gets an `at` stamp when it has none - the mail
    renders it as the time, and without it an alert carried no time at all.
    `email_for(admin)` picks the address per admin (None = bell only); the
    default is the admin's own. A dispatch that raises is logged and counted,
    never propagated."""
    if category == NotificationCategory.ops_alert and not payload.get("at"):
        payload = {**payload, "at": utc_now().isoformat()}
    pick_email = email_for or (lambda admin: admin.email)
    sent = failed = 0
    first_error: str | None = None
    for admin in enabled_admins(db):
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
