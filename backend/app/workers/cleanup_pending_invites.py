"""Daily cron: hard-delete pending/expired invites older than the
retention window.

Invites are 24h-TTL by design (services/invite.py::create_invite). After
that they're useless — the consume path refuses an expired token — but
the row stays in `invite_tokens` forever unless the admin clicks Delete.
This cron sweeps the abandoned ones so the table doesn't grow
monotonically.

Filter: ``used_user_id IS NULL AND created_at < (now - INVITE_RETENTION_DAYS)``.

- ``used_user_id IS NULL`` excludes consumed invites (those are
  proof-of-onboarding for the resulting user and stay for as long as the
  user row does).
- ``created_at < cutoff`` is the user-facing predicate ("14 days old").
  Since invites expire 24h after creation, this is effectively "expired
  for `INVITE_RETENTION_DAYS - 1` days."

Idempotent: re-running picks up no work once the matching set has been
deleted, because the predicate stays stable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from ..config import settings
from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.invite_token import InviteToken
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.cleanup_pending_invites")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@track_cron("cleanup_pending_invites")
async def cleanup_pending_invites(_ctx) -> dict:
    """Hard-delete pending/expired invites older than the retention
    window. Returns a small dict for diagnostics."""
    db = SessionLocal()
    try:
        cutoff = _utcnow() - timedelta(days=settings.INVITE_RETENTION_DAYS)
        result = db.execute(
            delete(InviteToken).where(
                InviteToken.used_user_id.is_(None),
                InviteToken.created_at < cutoff,
            )
        )
        purged = int(result.rowcount or 0)
        if purged:
            record_audit_event(
                db,
                event_type=AuditEventType.invite_purged,
                actor_user_id=None,
                target_type="invite_tokens",
                target_id=None,
                metadata={
                    "purged_count": purged,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": settings.INVITE_RETENTION_DAYS,
                },
            )
        db.commit()
        if purged:
            logger.info(
                "cleanup_pending_invites: purged=%d cutoff=%s",
                purged,
                cutoff.isoformat(),
            )
        return {"purged": purged}
    finally:
        db.close()
