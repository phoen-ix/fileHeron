"""Hourly cron: refresh-token housekeeping.

Two passes per run:

1. **Soft-revoke**: any refresh_token whose ``expires_at < now`` and is
   not yet revoked gets ``revoked_at`` set. This stops them appearing in
   the `/account/sessions` list (which already filters on
   `expires_at > now`, so this is mostly belt-and-suspenders) and gives
   a single timestamp for "when did this finally die" in the audit
   trail.

2. **Hard-delete**: any refresh_token whose ``revoked_at`` is older than
   ``REFRESH_TOKEN_RETENTION_DAYS`` (default 30) is removed. This bounds
   table growth on long-running deploys without losing forensic data
   inside the retention window.

Idempotent: re-running the job picks up no work the second time
because the predicates only match "active and stale" / "revoked and
old enough", both of which are stable once a row is processed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..database import SessionLocal
from ..models.refresh_token import RefreshToken
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.cleanup_expired_tokens")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@track_cron("cleanup_expired_tokens")
async def cleanup_expired_tokens(_ctx) -> dict:
    """Walk refresh_tokens; soft-revoke past-TTL rows + hard-delete
    revoked rows older than the retention window."""
    db = SessionLocal()
    soft_revoked = 0
    deleted = 0
    try:
        now = _utcnow()

        # 1. Past-TTL → soft-revoke. Bulk UPDATE with synchronize_session=False
        #    is safe because we don't use the affected rows in this session.
        soft_revoked = (
            db.query(RefreshToken)
            .filter(
                RefreshToken.expires_at < now,
                RefreshToken.revoked_at.is_(None),
            )
            .update({"revoked_at": now}, synchronize_session=False)
        )

        # 2. Revoked + past retention window → hard-delete.
        from ..services import settings_registry
        cutoff = now - timedelta(
            days=settings_registry.effective(db, settings_registry.K.REFRESH_TOKEN_RETENTION_DAYS)
        )
        deleted = (
            db.query(RefreshToken)
            .filter(
                RefreshToken.revoked_at.is_not(None),
                RefreshToken.revoked_at < cutoff,
            )
            .delete(synchronize_session=False)
        )

        db.commit()
        if soft_revoked or deleted:
            logger.info(
                "cleanup_expired_tokens: soft_revoked=%d deleted=%d",
                soft_revoked,
                deleted,
            )
        return {"soft_revoked": soft_revoked, "deleted": deleted}
    finally:
        db.close()
