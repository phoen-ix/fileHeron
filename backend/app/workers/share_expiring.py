"""Hourly: notify recipients (and the sender) that a share expires
in approximately 24 hours.

Idempotency: each share carries `expiring_notified_at`. We pick up only
shares with that column NULL whose `expires_at` falls in the (now+24h,
now+25h) window. The 25h ceiling keeps us from re-firing on shares
created with a 24h-and-a-bit expiry that the previous run already
notified about; the NULL check is the durable barrier.

Failures during dispatch are caught + logged so a single bad recipient
doesn't poison the whole batch — and the column is only marked once
the per-share dispatch loop completes (best-effort but bounded).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..database import SessionLocal
from ..models.notification import NotificationCategory
from ..models.share import Share, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User
from ..services import notification as notif_svc

logger = logging.getLogger("fileheron.workers.share_expiring")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


async def share_expiring_24h_warning(_ctx) -> dict:
    """Find active shares expiring in (now+24h, now+25h) without a prior
    notification, dispatch share_expiring to recipients + sender, mark
    expiring_notified_at."""
    db = SessionLocal()
    notified_shares = 0
    notified_users = 0
    try:
        now = _utcnow()
        lower = now + timedelta(hours=24)
        upper = now + timedelta(hours=25)
        shares = (
            db.query(Share)
            .filter(
                Share.state == ShareState.active,
                Share.expiring_notified_at.is_(None),
                Share.expires_at >= lower,
                Share.expires_at <= upper,
            )
            .all()
        )

        for share in shares:
            recipients = (
                db.query(ShareRecipient)
                .filter(ShareRecipient.share_id == share.id)
                .all()
            )
            target_user_ids: set[int] = {share.created_by_id}
            for r in recipients:
                if r.recipient_user_id is not None:
                    target_user_ids.add(r.recipient_user_id)
                # Group recipients are deliberately NOT fanned out here —
                # 50-member groups would generate 50 emails and the
                # group-share recipient sees the share in their inbox
                # anyway. Phase 6b can add a "I'm subscribed to group X
                # expiry alerts" toggle if needed.

            from ..services import site as site_svc
            payload = {
                "subject": share.subject,
                "expires_at": share.expires_at,
                "share_url": f"{site_svc.get_site_url(db)}/share/{share.id}",
            }
            for uid in target_user_ids:
                user = db.query(User).filter(User.id == uid).one_or_none()
                if user is None or user.is_disabled:
                    continue
                # email_hint is a masked hint, not a deliverable address.
                # Until we add a `users.email_plaintext_for_delivery`
                # column (we deliberately don't store plaintext), we
                # Plaintext email is now stored on the user row, so the
                # 24h warning actually goes out by email + in-app.
                payload_for_user = dict(payload)
                payload_for_user["recipient_name"] = user.display_name
                try:
                    notif_svc.dispatch(
                        db,
                        user=user,
                        category=NotificationCategory.share_expiring,
                        payload=payload_for_user,
                        link_url=payload["share_url"],
                        email_to=user.email,
                    )
                    notified_users += 1
                except Exception:
                    logger.exception(
                        "share_expiring dispatch failed for user=%d share=%s",
                        uid,
                        share.id,
                    )

            share.expiring_notified_at = now
            notified_shares += 1

        db.commit()
        if notified_shares:
            logger.info(
                "share_expiring: notified %d shares, %d users",
                notified_shares,
                notified_users,
            )
        return {
            "notified_shares": notified_shares,
            "notified_users": notified_users,
        }
    finally:
        db.close()
