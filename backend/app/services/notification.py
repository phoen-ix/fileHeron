"""Notification dispatch - single funnel for every user-visible event.

Every callsite (share creation, password reset, public-link download,
…) calls `dispatch(db, user, category, payload)`. This:

1. Inserts a row into `notifications` for the in-app bell. The row is
   always written regardless of email channel choice, so we have
   history even when the user has opted out of email for that category.
2. Reads the user's `user_notification_preferences` row for this
   category - defaulting to `both` when no row exists. If the channel
   includes email, renders the template via `services/email.py` and
   enqueues the `send_email_job` task.

Failures in either step are logged but don't propagate - a missing
notification should never fail the original action (creating a share,
resetting a password, …).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.notification import Notification, NotificationCategory
from ..models.user import User
from ..models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from . import email as email_svc
from . import job_queue

logger = logging.getLogger("fileheron.notification")

# Default channel for a category when the user has no preference row.
# Conservative-on-noise (`email`-only for ones that double-send via
# other surfaces) and `both` for the rest.
_DEFAULT_CHANNEL: dict[NotificationCategory, NotificationChannel] = {
    NotificationCategory.share_created: NotificationChannel.both,
    NotificationCategory.share_files_added: NotificationChannel.both,
    NotificationCategory.share_expiring: NotificationChannel.both,
    NotificationCategory.share_pending_approval: NotificationChannel.both,
    NotificationCategory.share_approved: NotificationChannel.both,
    NotificationCategory.share_rejected: NotificationChannel.both,
    NotificationCategory.public_link_downloaded: NotificationChannel.email,
    NotificationCategory.account_created: NotificationChannel.email,
    NotificationCategory.reset_password: NotificationChannel.email,
    NotificationCategory.login_alert: NotificationChannel.email,
    # Security notice - surface an SSO auto-link on both channels so an
    # unauthorised link is hard to miss.
    NotificationCategory.oidc_linked: NotificationChannel.both,
    NotificationCategory.file_quarantined: NotificationChannel.both,
    # Session-cap eviction: in_app only by default (informational; the user
    # can re-login any time). Users can opt into email via preferences.
    NotificationCategory.session_evicted: NotificationChannel.in_app,
    # Ops alerts: in-app only by default. On a busy system these fire in
    # bursts (cron retries, transient SMTP/AV issues); emailing each one
    # would mailstorm the admin's inbox. The /admin/system view + the
    # bell SSE rail are where operators look anyway. Admins can flip to
    # `both` via their notification preferences if they want pages.
    NotificationCategory.ops_alert: NotificationChannel.in_app,
    # New-release detected. Default `both` so admins get the email even
    # without opening the app; dedup at the call site means at most one
    # notification per detected version transition.
    NotificationCategory.release_available: NotificationChannel.both,
    # Inbound mail: in-app only (no stored admin plaintext email); the
    # `imap.notify_mode` setting decides whether it fires at all.
    NotificationCategory.inbound_message: NotificationChannel.in_app,
}


def _channel_for(
    db: Session, user_id: int, category: NotificationCategory
) -> NotificationChannel:
    pref = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.category == category,
        )
        .one_or_none()
    )
    if pref is not None:
        return pref.channel
    return _DEFAULT_CHANNEL.get(category, NotificationChannel.both)


def _wants_email(channel: NotificationChannel) -> bool:
    return channel in (NotificationChannel.email, NotificationChannel.both)


def _wants_in_app(channel: NotificationChannel) -> bool:
    return channel in (NotificationChannel.in_app, NotificationChannel.both)


def _json_safe(value: Any) -> Any:
    """Convert datetimes to ISO strings so the payload survives the JSON
    column. Original payload (with datetime instances) is what we pass
    to email rendering - Jinja's dt_locale filter wants datetimes."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def dispatch(
    db: Session,
    *,
    user: User,
    category: NotificationCategory,
    payload: dict[str, Any],
    link_url: str | None = None,
    template_slug: str | None = None,
    email_to: str | None = None,
) -> Notification | None:
    """Record + (maybe) send.

    `template_slug` defaults to the category value, which matches the
    file naming convention `{slug}.{txt|html}.j2`. Override only when a
    category fans out to multiple emails (rare).

    `email_to` is the plaintext recipient address. Callers pass it
    explicitly; the dispatcher doesn't auto-derive it from
    `user.email` because some callers (e.g. invite consume) have a
    freshly-supplied address that hasn't landed in `users.email`
    yet. The pre-`202605031600_email_plaintext` design stored only a
    masked hint and required callers to carry the plaintext through -
    the column-and-hint setup is gone but the `email_to` parameter
    shape stuck around.

    Caller commits."""
    if user.is_disabled:
        logger.info(
            "notification.dispatch: skipping disabled user %d (%s)",
            user.id,
            category.value,
        )
        return None

    channel = _channel_for(db, user.id, category)

    notif: Notification | None = None
    # Always write an in-app row when the channel is email-only too; the bell
    # becomes a permanent log of what was emailed. Only the explicit `off`
    # choice suppresses persistence.
    if (
        _wants_in_app(channel) or channel == NotificationChannel.email
    ) and channel != NotificationChannel.off:
        notif = Notification(
            user_id=user.id,
            category=category,
            payload_json=_json_safe(payload),
            link_url=link_url,
        )
        db.add(notif)
        db.flush()

        # Push live to SSE listeners (the bell). Best-effort - the
        # in-app row is the durable record; SSE is real-time UX only.
        if _wants_in_app(channel):
            from . import sse as sse_svc
            sse_svc.publish_sync(
                user.id,
                {
                    "event": "notification",
                    "id": notif.id,
                    "data": {
                        "id": notif.id,
                        "category": notif.category.value,
                        "link_url": notif.link_url,
                        "created_at": notif.created_at.isoformat()
                        if notif.created_at
                        else None,
                        "payload": notif.payload_json,
                    },
                },
            )

    if _wants_email(channel) and email_to:
        try:
            from . import mail_log
            from . import site as site_svc

            slug = template_slug or category.value
            # Render ONCE: the same (subject, text, html) is both logged (masked)
            # and enqueued (unmasked, for the real send) - never re-rendered, so
            # the stored body matches what was sent.
            subject, text, html = email_svc.render_email(
                user.locale, slug, payload,
                app_url=site_svc.get_site_url(db),
                site_timezone=site_svc.get_site_timezone(db),
                app_name=site_svc.get_app_name(db),
                db=db,
            )
            eid = mail_log.record_queued(
                db,
                recipient_email=email_to,
                recipient_user_id=user.id,
                category=category.value,
                template_slug=slug,
                subject=subject,
                text_body=text,
                html_body=html,
            )
            job_queue.enqueue(
                "send_email_job",
                to=email_to,
                subject=subject,
                text_body=text,
                html_body=html,
                email_log_id=eid,
            )
        except Exception:
            logger.exception(
                "failed to render/enqueue email for category=%s user_id=%d",
                category.value,
                user.id,
            )

    return notif
