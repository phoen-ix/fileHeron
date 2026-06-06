"""Hourly: notify recipients (and the sender) that a share expires
in approximately 24 hours.

Idempotency: each share carries `expiring_notified_at`. We pick up only
shares with that column NULL whose `expires_at` falls in the (now+24h,
now+25h) window. The 25h ceiling keeps us from re-firing on shares
created with a 24h-and-a-bit expiry that the previous run already
notified about; the NULL check is the durable barrier.

Failures during dispatch are caught + logged so a single bad recipient
doesn't poison the whole batch - and the column is only marked once
the per-share dispatch loop completes (best-effort but bounded).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.notification import NotificationCategory
from ..models.share import Share, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User
from ..services import notification as notif_svc
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.share_expiring")




@track_cron("share_expiring_24h_warning")
async def share_expiring_24h_warning(_ctx) -> dict:
    """Find active shares expiring in (now+24h, now+25h) without a prior
    notification, dispatch share_expiring to recipients + sender, mark
    expiring_notified_at."""
    db = SessionLocal()
    notified_shares = 0
    notified_users = 0
    try:
        now = utc_now()
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

        # Bulk-load recipients for every share + every candidate user in two
        # queries (was per-share recipients + per-recipient user lookups).
        share_ids = [s.id for s in shares]
        recipients_by_share: dict[str, list[int]] = {}
        if share_ids:
            for r in (
                db.query(ShareRecipient)
                .filter(ShareRecipient.share_id.in_(share_ids))
                .all()
            ):
                # Group recipients are deliberately NOT fanned out here -
                # 50-member groups would generate 50 emails and the
                # group-share recipient sees the share in their inbox anyway.
                if r.recipient_user_id is not None:
                    recipients_by_share.setdefault(r.share_id, []).append(
                        r.recipient_user_id
                    )

        all_user_ids = {s.created_by_id for s in shares}
        for ids in recipients_by_share.values():
            all_user_ids.update(ids)
        users_by_id: dict[int, User] = {
            u.id: u
            for u in db.query(User).filter(User.id.in_(all_user_ids)).all()
        } if all_user_ids else {}

        from ..services import site as site_svc
        site_url = site_svc.get_site_url(db)

        for share in shares:
            target_user_ids: set[int] = {share.created_by_id}
            target_user_ids.update(recipients_by_share.get(share.id, []))

            payload = {
                "subject": share.subject,
                "expires_at": share.expires_at,
                "share_url": f"{site_url}/share/{share.id}",
            }
            for uid in target_user_ids:
                user = users_by_id.get(uid)
                if user is None or user.is_disabled:
                    continue
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
            # Commit per-share so a downstream failure doesn't lose the
            # idempotency marker for shares we already notified about.
            try:
                db.commit()
                notified_shares += 1
            except Exception:
                db.rollback()
                logger.exception(
                    "share_expiring commit failed for share=%s", share.id
                )

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
