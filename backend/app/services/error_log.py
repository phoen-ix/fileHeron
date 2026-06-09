"""Persist captured server errors to the ``error_log`` table + read it back.

Logging is **decoupled from alerting**: the ``notify_admin_error`` worker calls
:func:`record` for every qualifying error *before* the alert saferails in
``services/error_alert.py`` run, so the browsable log is complete even when the
matching email was deduped, hourly-capped, or the alert feature is off.

What qualifies (``should_log``):
- HTTP 5xx + failed crons -> logged whenever ``error_log.enabled`` (default on).
- HTTP 4xx -> logged only when ``error_log.capture_4xx`` is on AND the status is
  in the ``error_log.http_4xx_codes`` allowlist (empty allowlist = capture
  nothing, so a stray toggle can't flood the log with 401/404/422 noise).

Everything is fail-open: a logging failure must never break the worker job.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.error_log import ErrorLog
from ..utils.timeutil import utc_now
from . import settings as settings_svc

logger = logging.getLogger("fileheron.error_log")

K = settings_svc.Keys


def parse_4xx_codes(raw: str | None) -> set[int]:
    """Parse a CSV of HTTP status codes; keep only valid 4xx values."""
    out: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 400 <= n < 500:
            out.add(n)
    return out


# ---------------------------------------------------------------------------
# Capture policy
# ---------------------------------------------------------------------------


def should_log(db: Session, event: dict[str, Any]) -> bool:
    """Whether this event should be persisted (authoritative; the worker uses it)."""
    if not settings_svc.get_bool(db, K.ERROR_LOG_ENABLED, default=True):
        return False
    source = event.get("source")
    if source == "worker":
        return True
    status = int(event.get("status_code") or 0)
    if status >= 500:
        return True
    if 400 <= status < 500:
        if not settings_svc.get_bool(db, K.ERROR_LOG_CAPTURE_4XX, default=False):
            return False
        allow = parse_4xx_codes(settings_svc.get(db, K.ERROR_LOG_4XX_CODES))
        return status in allow  # empty allowlist => capture nothing
    return False


# Tiny in-process TTL cache so the request-path middleware can decide whether to
# enqueue a 4xx without a DB read per error. ~60s lag after a toggle is fine.
_CACHE_TTL_SEC = 60.0
_cache_value = False
_cache_expires = 0.0


def capture_4xx_enabled_cached() -> bool:
    """Cheap, cached: should the middleware enqueue 4xx at all? True only when
    capture is on AND the allowlist is non-empty. Fail-closed."""
    global _cache_value, _cache_expires
    now = time.monotonic()
    if now < _cache_expires:
        return _cache_value
    value = False
    db = SessionLocal()
    try:
        if settings_svc.get_bool(db, K.ERROR_LOG_CAPTURE_4XX, default=False):
            value = bool(parse_4xx_codes(settings_svc.get(db, K.ERROR_LOG_4XX_CODES)))
    except Exception:
        logger.warning("error_log: capture_4xx cache refresh failed", exc_info=True)
        value = False
    finally:
        db.close()
    _cache_value = value
    _cache_expires = now + _CACHE_TTL_SEC
    return value


def _reset_cache() -> None:
    """Test hook: drop the cached 4xx-capture flag."""
    global _cache_expires
    _cache_expires = 0.0


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def record(db: Session, event: dict[str, Any], *, signature: str) -> int | None:
    """Insert one error_log row. Returns its id (caller commits) or None on
    failure. Never raises."""
    try:
        at_raw = event.get("at")
        if isinstance(at_raw, datetime):
            created = at_raw
        elif isinstance(at_raw, str):
            try:
                created = datetime.fromisoformat(at_raw)
            except ValueError:
                created = utc_now()
        else:
            created = utc_now()

        def _clip(v: Any, n: int) -> str | None:
            if v is None:
                return None
            s = str(v)
            return s[:n] if s else None

        row = ErrorLog(
            created_at=created,
            source=_clip(event.get("source") or "http", 16),
            status_code=int(event.get("status_code") or 0),
            code=_clip(event.get("code") or "UNKNOWN", 64),
            exception_type=_clip(event.get("exception_type"), 128),
            message=_clip(event.get("message"), 500),
            method=_clip(event.get("method"), 8),
            path=_clip(event.get("path"), 512),
            job_name=_clip(event.get("job_name"), 128),
            request_id=_clip(event.get("request_id"), 64),
            user_id=event.get("user_id"),
            auth_via=_clip(event.get("auth_via"), 16),
            signature=signature,
            alerted=False,
        )
        db.add(row)
        db.flush()
        return row.id
    except Exception:
        logger.exception("error_log.record failed")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def mark_alerted(db: Session, row_id: int | None) -> None:
    """Flag a logged row as having triggered an alert email. Caller commits."""
    if not row_id:
        return
    try:
        db.query(ErrorLog).filter(ErrorLog.id == row_id).update(
            {ErrorLog.alerted: True}, synchronize_session=False
        )
    except Exception:
        logger.exception("error_log.mark_alerted failed for id=%s", row_id)


# ---------------------------------------------------------------------------
# Read (admin viewer)
# ---------------------------------------------------------------------------


def filtered_query(
    db: Session,
    *,
    code: str | None = None,
    status_code: int | None = None,
    source: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
):
    q = db.query(ErrorLog)
    if code:
        q = q.filter(ErrorLog.code == code)
    if status_code is not None:
        q = q.filter(ErrorLog.status_code == status_code)
    if source:
        q = q.filter(ErrorLog.source == source)
    if from_ts:
        q = q.filter(ErrorLog.created_at >= from_ts)
    if to_ts:
        q = q.filter(ErrorLog.created_at <= to_ts)
    return q


def list_errors(
    db: Session,
    *,
    code: str | None = None,
    status_code: int | None = None,
    source: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ErrorLog], int]:
    q = filtered_query(
        db, code=code, status_code=status_code, source=source, from_ts=from_ts, to_ts=to_ts
    )
    total = q.count()
    rows = (
        q.order_by(ErrorLog.created_at.desc(), ErrorLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def get(db: Session, row_id: int) -> ErrorLog | None:
    return db.get(ErrorLog, row_id)
