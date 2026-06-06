"""Hourly heuristic anomaly scan (v1.20.0).

Runs at minute :33. Reads admin-tunable thresholds, runs the three GeoIP-free
detectors in services/anomaly.py, and for each finding (deduped 1h per
type+subject, mirroring ops_check) records an `anomaly_detected` audit event
(which auto-fires subscribed webhooks) and dispatches an `ops_alert` to every
admin. Advisory only - it never blocks anyone. Disabled wholesale by the
`anomaly.enabled` kv switch.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..services import anomaly as anomaly_svc
from ..services import settings as settings_svc
from ..services import settings_registry as _sr
from ..services.audit import record_audit_event
from ..services.cron_tracker import track_cron
from ..services.notification import dispatch
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.anomaly_check")

_DEDUP_TTL_SEC = 3600
_MASS_DOWNLOAD_WINDOW_MIN = 15
_MULTI_NETWORK_WINDOW_MIN = 30
_LOGIN_FAILURE_WINDOW_MIN = 15


def _dedup_seen(finding) -> bool:
    """True if this (type, subject) was already alerted in the dedup window.
    Best-effort: Redis down → False (better noisy than silent)."""
    try:
        redis = get_redis()
        key = f"fh:anomaly:{finding.type}:{finding.subject}"
        if redis.exists(key):
            return True
        redis.set(key, "1", ex=_DEDUP_TTL_SEC)
        return False
    except Exception:
        return False


def _alert_admins(db, finding) -> None:
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    payload = {
        "reason": "anomaly",
        "type": finding.type,
        "subject": finding.subject,
        "count": finding.count,
        "detail": finding.detail,
        "at": utc_now().isoformat(),
    }
    for admin in admins:
        try:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/audit-log",
            )
        except Exception:
            logger.exception("anomaly ops_alert dispatch failed admin=%d", admin.id)


@track_cron("anomaly_check")
async def anomaly_check(_ctx) -> dict:
    db = SessionLocal()
    try:
        if not settings_svc.get_bool(db, settings_svc.Keys.ANOMALY_ENABLED, default=True):
            return {"enabled": False}

        now = utc_now()
        mass_threshold = _sr.effective(db, _sr.K.ANOMALY_MASS_DOWNLOAD_THRESHOLD)
        net_threshold = _sr.effective(db, _sr.K.ANOMALY_MULTI_NETWORK_THRESHOLD)
        login_threshold = _sr.effective(db, _sr.K.ANOMALY_LOGIN_FAILURE_THRESHOLD)

        findings = []
        for run in (
            lambda: anomaly_svc.mass_download(
                db, cutoff=now - timedelta(minutes=_MASS_DOWNLOAD_WINDOW_MIN), threshold=mass_threshold
            ),
            lambda: anomaly_svc.multi_network(
                db, cutoff=now - timedelta(minutes=_MULTI_NETWORK_WINDOW_MIN), threshold=net_threshold
            ),
            lambda: anomaly_svc.login_stuffing(
                db, cutoff=now - timedelta(minutes=_LOGIN_FAILURE_WINDOW_MIN), threshold=login_threshold
            ),
        ):
            try:
                findings.extend(run())
            except Exception:
                logger.exception("anomaly detector failed")

        alerted = 0
        for f in findings:
            if _dedup_seen(f):
                continue
            record_audit_event(
                db,
                event_type=AuditEventType.anomaly_detected,
                actor_user_id=None,
                target_type=f.type,
                target_id=f.subject,
                metadata={"count": f.count, **f.detail},
            )
            _alert_admins(db, f)
            alerted += 1

        db.commit()
        return {"findings": len(findings), "alerted": alerted}
    finally:
        db.close()
