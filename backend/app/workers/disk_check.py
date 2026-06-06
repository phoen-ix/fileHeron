"""Hourly disk-space monitor.

Runs at minute :19 — after ops_check (:15), before cleanup_expired_tokens (:23).
Reads free space on STORAGE_ROOT and maintains the `storage.critical_low` kv
flag that routers/uploads.py gates new uploads on:

- On transition healthy → critical: flip the flag to true + dispatch an
  `ops_alert` to every non-disabled admin (deduped 1h via Redis).
- On transition critical → healthy: flip the flag back to false + clear the
  dedup key so a future dip re-alerts immediately.

Downloads never consult the flag — only new uploads are refused, so a full
disk degrades gracefully instead of 500-ing and orphaning quota reservations.
"""
from __future__ import annotations

import logging

from ..config import settings
from ..database import SessionLocal
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..services import settings as settings_svc
from ..services import storage as storage_svc
from ..services.cron_tracker import track_cron
from ..services.notification import dispatch
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.disk_check")

_DEDUP_KEY = "fh:ops:alert:storage_critical_low"
_DEDUP_TTL_SEC = 3600


def _dedup_seen() -> bool:
    """True if we've already alerted within the dedup window. Best-effort:
    Redis down → False (better noisy than silent)."""
    try:
        redis = get_redis()
        if redis.exists(_DEDUP_KEY):
            return True
        redis.set(_DEDUP_KEY, "1", ex=_DEDUP_TTL_SEC)
        return False
    except Exception:
        return False


def _clear_dedup() -> None:
    try:
        get_redis().delete(_DEDUP_KEY)
    except Exception:
        pass


def _alert_admins(db, *, payload: dict) -> int:
    if _dedup_seen():
        return 0
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
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

    from ..services import webhook as webhook_svc
    webhook_svc.emit(
        db, webhook_svc.OPS_ALERT_EVENT,
        {"target_type": "ops", "target_id": "storage_critical_low", "metadata": payload},
    )
    return n


@track_cron("disk_check")
async def disk_check(_ctx) -> dict:
    from ..services.storage_backend import get_storage_backend
    if not get_storage_backend().supports_disk_stats:
        # Object-store backend — "local disk full" is meaningless; nothing to do.
        return {"skipped": True, "reason": "non-disk backend"}
    db = SessionLocal()
    try:
        stats = storage_svc.get_disk_stats(settings.STORAGE_ROOT)
        if "error" in stats:
            logger.warning("disk_check: statvfs error: %s", stats.get("error"))
            return {"error": stats.get("error")}

        is_critical = storage_svc.is_storage_critical_low(db, settings.STORAGE_ROOT)
        was_critical = settings_svc.get_bool(
            db, settings_svc.Keys.STORAGE_CRITICAL_LOW, default=False
        )

        dispatched = 0
        if is_critical != was_critical:
            settings_svc.set_value(
                db,
                key=settings_svc.Keys.STORAGE_CRITICAL_LOW,
                value="true" if is_critical else "false",
                actor=None,
            )
            db.commit()
            if is_critical:
                dispatched = _alert_admins(
                    db,
                    payload={
                        "reason": "storage_critical_low",
                        "free_bytes": stats["free_bytes"],
                        "percent_free": round(stats["percent_free"], 2),
                        "at": utc_now().isoformat(),
                    },
                )
                db.commit()
            else:
                _clear_dedup()

        return {
            "free_bytes": stats["free_bytes"],
            "percent_free": round(stats["percent_free"], 2),
            "is_critical": is_critical,
            "transitioned": is_critical != was_critical,
            "dispatched": dispatched,
        }
    finally:
        db.close()
