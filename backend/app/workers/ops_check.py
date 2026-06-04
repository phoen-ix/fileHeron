"""Hourly ops-health probe.

Closes the "no operator alerting" gap from the operational audit. Runs at
minute :15, after the other crons have had a chance to fire (and the
cron_tracker has recorded any of their failures). Checks:

- AV reachability (via av_scan.ping)
- Redis reachability (ping)
- Recent SMTP undeliverable count in the audit_log (last 1h)

For each unhealthy signal, dispatches an `ops_alert` notification to
every non-disabled admin. De-duplicated via Redis keys
`fh:ops:alert:<reason>` with a 1h TTL so we don't spam admins on
extended outages.

The cron_tracker decorator records this run too, so the System view
can show "ops_check last ran 23 minutes ago".
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.audit_log import AuditEventType, AuditLog
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..services.cron_tracker import track_cron
from ..services.notification import dispatch
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.ops_check")

_DEDUP_TTL_SEC = 3600
_SMTP_FAILURE_LOOKBACK = timedelta(hours=1)




def _dedup_seen(reason: str) -> bool:
    """Return True if we've already dispatched this reason in the dedup
    window. Best-effort: if Redis is down, return False (better noisy)."""
    try:
        redis = get_redis()
        key = f"fh:ops:alert:{reason}"
        if redis.exists(key):
            return True
        redis.set(key, "1", ex=_DEDUP_TTL_SEC)
        return False
    except Exception:
        return False


def _alert_admins(db, *, reason: str, detail: str) -> int:
    if _dedup_seen(reason):
        return 0
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    payload = {"reason": reason, "detail": detail[:500], "at": utc_now().isoformat()}
    n = 0
    for admin in admins:
        try:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/system",
            )
            n += 1
        except Exception:
            logger.exception("ops_alert dispatch failed admin=%d", admin.id)
    return n


def _check_av(db) -> str | None:
    """Return None if AV is healthy, an error string otherwise."""
    try:
        from ..config import settings
        if getattr(settings, "AV_SKIP", False):
            return None
        from ..services import av_scan
        if av_scan.ping():
            return None
        return "ClamAV ping returned false"
    except Exception as e:
        return f"ClamAV check raised: {e}"


def _check_redis() -> str | None:
    try:
        get_redis().ping()
        return None
    except Exception as e:
        return f"Redis ping raised: {e}"


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
    dispatched = 0
    summary = {"av": "ok", "redis": "ok", "smtp": "ok"}
    try:
        av_err = _check_av(db)
        if av_err:
            summary["av"] = av_err
            dispatched += _alert_admins(db, reason="av_unhealthy", detail=av_err)

        redis_err = _check_redis()
        if redis_err:
            summary["redis"] = redis_err
            dispatched += _alert_admins(db, reason="redis_unhealthy", detail=redis_err)

        smtp_err = _check_smtp(db)
        if smtp_err:
            summary["smtp"] = smtp_err
            dispatched += _alert_admins(db, reason="smtp_failing", detail=smtp_err)

        db.commit()
        return {"dispatched": dispatched, **summary}
    finally:
        db.close()
