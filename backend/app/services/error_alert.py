"""Email admins when server-side errors occur (configurable, anti-flood).

Two sources feed this, both via the ``notify_admin_error`` ARQ job:
- HTTP 5xx responses (the exception handlers in ``middleware/errors.py``).
- Failed scheduled tasks (``services/cron_tracker.py``), gated per-task by the
  ``cron.<name>.alert_on_failure`` flag set on the Scheduled tasks page.

The job hands every error event to :func:`handle_error_event`, which applies the
admin-tunable saferails and (maybe) sends:

- **Master switch** ``error_alert.enabled`` (off by default) gates everything.
- **Per-source gate** - HTTP uses ``error_alert.source_http_5xx``; worker uses
  the per-cron flag.
- **Cooldown dedup** - one email per error *signature* per cooldown window
  (Redis SET-with-TTL, mirrors ``cron_tracker``). Repeats inside the window are
  counted, not emailed; the next post-cooldown email reports how many were
  suppressed.
- **Hourly cap** - a global ceiling on alert emails per hour regardless of
  signature, via the shared :func:`rate_limit.check_ip_allowed` sliding window.
- **Recipients** - all non-disabled admins (honoring per-admin notification
  prefs) OR a custom address list.

Everything here is fail-open and never raises out: a failure to alert must never
break the request that errored or the worker job that called us. On a Redis
outage the cooldown errs toward *sending* (better noisy than silent), bounded by
the hourly cap's in-process fallback so it can't mailstorm.

Emails carry context only - exception type, message, method/path, status/code,
request_id, acting user id, timestamp, occurrence count - never a traceback,
request body, query string, or headers.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..utils.crypto import sha256_hex
from ..utils.timeutil import utc_now
from . import error_log as error_log_svc
from . import rate_limit, settings_registry
from . import settings as settings_svc

logger = logging.getLogger("fileheron.error_alert")

K = settings_svc.Keys

# Redis key prefixes. `sent` is the per-signature cooldown gate; `total` is a
# longer-lived occurrence counter so a post-cooldown email can report how many
# repeats were suppressed; `reported`/`lastsent` carry the last-email watermark.
_SENT_KEY = "fh:erralert:sent:{sig}"
_TOTAL_KEY = "fh:erralert:total:{sig}"
_REPORTED_KEY = "fh:erralert:reported:{sig}"
_LASTSENT_KEY = "fh:erralert:lastsent:{sig}"
# total/reported/lastsent live well past one cooldown window so the suppressed
# count survives a quiet gap, but still expire so a one-off error doesn't leak
# keys forever.
_ACCOUNTING_TTL_SEC = 24 * 3600

# Global hourly cap bucket (constant "ip" - one shared window for all alerts).
_CAP_BUCKET = "err_alert_send"
_CAP_WINDOW_SEC = 3600

_NUM_SEG = re.compile(r"^\d+$")
_UUID_SEG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_HEX_SEG = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)  # tus ids, tokens, etc.


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def _normalize_path(path: str | None) -> str:
    """Collapse per-resource path segments (ids/uuids/hex tokens) to ``:id`` so
    /api/files/123 and /api/files/456 share one signature (one alert per
    endpoint shape, not per resource)."""
    out: list[str] = []
    for seg in (path or "").split("/"):
        if _NUM_SEG.match(seg) or _UUID_SEG.match(seg) or _HEX_SEG.match(seg):
            out.append(":id")
        else:
            out.append(seg)
    return "/".join(out)


def signature(event: dict[str, Any]) -> str:
    source = event.get("source") or ""
    exc_type = event.get("exception_type") or ""
    status = event.get("status_code") or ""
    code = event.get("code") or ""
    if source == "worker":
        path = event.get("job_name") or event.get("path") or ""
    else:
        path = _normalize_path(event.get("path"))
    return sha256_hex(f"{source}|{exc_type}|{path}|{status}|{code}")[:16]


# ---------------------------------------------------------------------------
# Saferails
# ---------------------------------------------------------------------------


class _CooldownDecision:
    __slots__ = ("should_send", "occurrence_count", "suppressed_count", "suppressed_since")

    def __init__(
        self,
        should_send: bool,
        occurrence_count: int,
        suppressed_count: int,
        suppressed_since: datetime | None,
    ) -> None:
        self.should_send = should_send
        self.occurrence_count = occurrence_count
        self.suppressed_count = suppressed_count
        self.suppressed_since = suppressed_since


def _cooldown_decision(sig: str, cooldown_sec: int) -> _CooldownDecision:
    """Decide whether this occurrence sends. Redis-backed; on any Redis error
    err toward sending (the hourly cap still bounds the blast radius)."""
    try:
        redis = get_redis()
        total = redis.incr(_TOTAL_KEY.format(sig=sig))
        redis.expire(_TOTAL_KEY.format(sig=sig), _ACCOUNTING_TTL_SEC)
        first = redis.set(_SENT_KEY.format(sig=sig), "1", nx=True, ex=cooldown_sec)
        if not first:
            # Already alerted for this signature inside the window. Count it,
            # don't email it.
            return _CooldownDecision(False, int(total), 0, None)

        reported_raw = redis.get(_REPORTED_KEY.format(sig=sig))
        reported = int(reported_raw) if reported_raw else 0
        last_iso = redis.get(_LASTSENT_KEY.format(sig=sig))
        now = utc_now()
        # Occurrences this email represents = everything since the last email.
        occurrence_count = max(1, int(total) - reported)
        suppressed_count = max(0, occurrence_count - 1)
        suppressed_since: datetime | None = None
        if suppressed_count > 0 and last_iso:
            try:
                suppressed_since = datetime.fromisoformat(last_iso)
            except ValueError:
                suppressed_since = None
        redis.set(_REPORTED_KEY.format(sig=sig), str(int(total)), ex=_ACCOUNTING_TTL_SEC)
        redis.set(_LASTSENT_KEY.format(sig=sig), now.isoformat(), ex=_ACCOUNTING_TTL_SEC)
        return _CooldownDecision(True, occurrence_count, suppressed_count, suppressed_since)
    except Exception:
        logger.warning("error_alert: cooldown check skipped (redis); sending", exc_info=True)
        return _CooldownDecision(True, 1, 0, None)


def _within_hourly_cap(db: Session) -> bool:
    cap = settings_registry.effective(db, K.ERROR_ALERT_MAX_PER_HOUR)
    # check_ip_allowed INCRs the window and returns False once over the limit;
    # on a Redis outage it falls back to the bounded in-process limiter.
    return rate_limit.check_ip_allowed(_CAP_BUCKET, "global", limit=int(cap), window_sec=_CAP_WINDOW_SEC)


# ---------------------------------------------------------------------------
# Recipients + send
# ---------------------------------------------------------------------------


def _parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        addr = part.strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def _build_payload(event: dict[str, Any], decision: _CooldownDecision) -> dict[str, Any]:
    at_raw = event.get("at")
    at_dt: datetime
    if isinstance(at_raw, str):
        try:
            at_dt = datetime.fromisoformat(at_raw)
        except ValueError:
            at_dt = utc_now()
    else:
        at_dt = utc_now()
    return {
        "source": event.get("source"),
        "exception_type": event.get("exception_type"),
        "message": event.get("message"),
        "method": event.get("method"),
        "path": event.get("path"),
        "job_name": event.get("job_name"),
        "status_code": event.get("status_code"),
        "code": event.get("code"),
        "ip": event.get("ip"),
        "request_id": event.get("request_id"),
        "user_id": event.get("user_id"),
        "auth_via": event.get("auth_via"),
        "at": at_dt,
        "occurrence_count": decision.occurrence_count,
        "suppressed_count": decision.suppressed_count,
        "suppressed_since": decision.suppressed_since,
    }


def _send_to_admins(db: Session, payload: dict[str, Any]) -> int:
    from . import notification as notif_svc

    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    sent = 0
    for admin in admins:
        try:
            notif_svc.dispatch(
                db,
                user=admin,
                category=NotificationCategory.server_error,
                payload=payload,
                link_url="/admin/system",
                email_to=admin.email,
            )
            sent += 1
        except Exception:
            logger.exception("error_alert: dispatch to admin=%d failed", admin.id)
    # Commit so the dispatcher's after-commit hooks (email enqueue + bell SSE) fire.
    db.commit()
    return sent


def _send_to_custom(db: Session, payload: dict[str, Any], addrs: list[str]) -> int:
    from . import email as email_svc
    from . import job_queue, mail_log
    from . import site as site_svc

    if not addrs:
        return 0
    # Custom addresses aren't users -> no per-user prefs, no unsubscribe footer.
    # Render once (no per-recipient footer) and enqueue a send per address.
    subject, text, html = email_svc.render_email(
        "en",
        "server_error",
        payload,
        app_url=site_svc.get_site_url(db),
        site_timezone=site_svc.get_site_timezone(db),
        app_name=site_svc.get_app_name(db),
        db=db,
    )
    sent = 0
    for addr in addrs:
        eid = mail_log.record_queued(
            db,
            recipient_email=addr,
            recipient_user_id=None,
            category=NotificationCategory.server_error.value,
            template_slug="server_error",
            subject=subject,
            text_body=text,
            html_body=html,
        )
        db.commit()
        try:
            job_queue.enqueue(
                "send_email_job",
                to=addr,
                subject=subject,
                text_body=text,
                html_body=html,
                email_log_id=eid,
            )
            sent += 1
        except Exception:
            logger.exception("error_alert: send_email enqueue failed for %s", addr)
    return sent


# ---------------------------------------------------------------------------
# Entry point (called by the notify_admin_error worker job)
# ---------------------------------------------------------------------------


def _alert_source_enabled(db: Session, event: dict[str, Any]) -> bool:
    """Whether the per-source alert toggle permits emailing this event. 4xx alerts
    ride the same allowlist that governs 4xx capture (alert is a subset of log)."""
    source = event.get("source")
    if source == "worker":
        job_name = event.get("job_name") or ""
        return settings_svc.get_bool(db, f"cron.{job_name}.alert_on_failure", default=False)
    if source == "http":
        status = int(event.get("status_code") or 0)
        if status >= 500:
            return settings_svc.get_bool(db, K.ERROR_ALERT_SOURCE_HTTP_5XX, default=True)
        if 400 <= status < 500:
            if not settings_svc.get_bool(db, K.ERROR_ALERT_SOURCE_HTTP_4XX, default=False):
                return False
            allow = error_log_svc.parse_4xx_codes(settings_svc.get(db, K.ERROR_LOG_4XX_CODES))
            return status in allow
    return False


def handle_error_event(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    """Persist to error_log (always, when logging is on) then maybe email admins.
    Logging and alerting are decoupled and independently fail-open. Never raises."""
    sig = signature(event)

    # STEP 1 - LOG. The browsable log captures every qualifying error regardless
    # of the alert switches / cooldown / cap below.
    row_id: int | None = None
    try:
        if error_log_svc.should_log(db, event):
            row_id = error_log_svc.record(db, event, signature=sig)
            db.commit()
    except Exception:
        logger.exception("error_alert: log step failed")
        try:
            db.rollback()
        except Exception:
            pass

    # STEP 2 - ALERT (the throttled subset).
    try:
        result = _maybe_alert(db, event, sig, row_id)
    except Exception:
        logger.exception("error_alert.handle_error_event failed")
        try:
            db.rollback()
        except Exception:
            pass
        result = {"status": "error"}
    result["logged"] = row_id is not None
    return result


def _maybe_alert(
    db: Session, event: dict[str, Any], sig: str, row_id: int | None
) -> dict[str, Any]:
    if not settings_svc.get_bool(db, K.ERROR_ALERT_ENABLED, default=False):
        return {"status": "disabled"}
    if event.get("source") not in ("http", "worker"):
        return {"status": "unknown_source"}
    if not _alert_source_enabled(db, event):
        return {"status": "source_disabled"}

    cooldown_sec = int(settings_registry.effective(db, K.ERROR_ALERT_COOLDOWN_MINUTES)) * 60
    decision = _cooldown_decision(sig, cooldown_sec)
    if not decision.should_send:
        return {"status": "deduped", "occurrences": decision.occurrence_count}

    # Only consume a cap slot once we've decided we'd send (suppressed repeats
    # must not burn the hourly budget).
    if not _within_hourly_cap(db):
        return {"status": "rate_capped"}

    payload = _build_payload(event, decision)
    mode = (settings_svc.get(db, K.ERROR_ALERT_RECIPIENTS_MODE) or "admins").strip().lower()
    if mode == "custom":
        addrs = _parse_recipients(settings_svc.get(db, K.ERROR_ALERT_CUSTOM_RECIPIENTS))
        sent = _send_to_custom(db, payload, addrs)
    else:
        sent = _send_to_admins(db, payload)
    if sent and row_id:
        error_log_svc.mark_alerted(db, row_id)
        db.commit()
    return {"status": "sent", "signature": sig, "recipients": sent}


# ---------------------------------------------------------------------------
# Settings snapshot / apply (service-not-router: the admin router delegates here)
# ---------------------------------------------------------------------------


def get_settings(db: Session) -> dict[str, Any]:
    return {
        "enabled": settings_svc.get_bool(db, K.ERROR_ALERT_ENABLED, default=False),
        "source_http_5xx": settings_svc.get_bool(db, K.ERROR_ALERT_SOURCE_HTTP_5XX, default=True),
        "source_http_4xx": settings_svc.get_bool(db, K.ERROR_ALERT_SOURCE_HTTP_4XX, default=False),
        "recipients_mode": (
            settings_svc.get(db, K.ERROR_ALERT_RECIPIENTS_MODE) or "admins"
        ),
        "custom_recipients": _parse_recipients(
            settings_svc.get(db, K.ERROR_ALERT_CUSTOM_RECIPIENTS)
        ),
        "cooldown_minutes": int(settings_registry.effective(db, K.ERROR_ALERT_COOLDOWN_MINUTES)),
        "max_per_hour": int(settings_registry.effective(db, K.ERROR_ALERT_MAX_PER_HOUR)),
        # Logging (decoupled from alerting).
        "log_enabled": settings_svc.get_bool(db, K.ERROR_LOG_ENABLED, default=True),
        "capture_4xx": settings_svc.get_bool(db, K.ERROR_LOG_CAPTURE_4XX, default=False),
        "http_4xx_codes": sorted(
            error_log_svc.parse_4xx_codes(settings_svc.get(db, K.ERROR_LOG_4XX_CODES))
        ),
        "retention_days": int(settings_registry.effective(db, K.ERROR_LOG_RETENTION_DAYS)),
    }


def update_settings(
    db: Session,
    *,
    enabled: bool,
    source_http_5xx: bool,
    source_http_4xx: bool,
    recipients_mode: str,
    custom_recipients: list[str],
    cooldown_minutes: int,
    max_per_hour: int,
    log_enabled: bool,
    capture_4xx: bool,
    http_4xx_codes: list[int],
    retention_days: int,
    actor: User,
    request=None,
) -> dict[str, Any]:
    """Persist all error-alert + error-log settings. Caller commits. Numeric
    bounds are enforced by the registry's ``coerce_for_store`` (clamped)."""
    for key, flag in (
        (K.ERROR_ALERT_ENABLED, enabled),
        (K.ERROR_ALERT_SOURCE_HTTP_5XX, source_http_5xx),
        (K.ERROR_ALERT_SOURCE_HTTP_4XX, source_http_4xx),
        (K.ERROR_LOG_ENABLED, log_enabled),
        (K.ERROR_LOG_CAPTURE_4XX, capture_4xx),
    ):
        settings_svc.set_value(
            db, key=key, value="true" if flag else "false", actor=actor, request=request,
        )
    settings_svc.set_value(
        db, key=K.ERROR_ALERT_RECIPIENTS_MODE, value=recipients_mode,
        actor=actor, request=request,
    )
    settings_svc.set_value(
        db, key=K.ERROR_ALERT_CUSTOM_RECIPIENTS,
        value=",".join(_parse_recipients(",".join(custom_recipients))),
        actor=actor, request=request,
    )
    # Normalise the 4xx allowlist to a sorted CSV of valid 4xx codes.
    valid = sorted(error_log_svc.parse_4xx_codes(",".join(str(c) for c in http_4xx_codes)))
    settings_svc.set_value(
        db, key=K.ERROR_LOG_4XX_CODES, value=",".join(str(c) for c in valid),
        actor=actor, request=request,
    )
    for key, value in (
        (K.ERROR_ALERT_COOLDOWN_MINUTES, cooldown_minutes),
        (K.ERROR_ALERT_MAX_PER_HOUR, max_per_hour),
        (K.ERROR_LOG_RETENTION_DAYS, retention_days),
    ):
        spec = settings_registry.BY_KEY[key]
        settings_svc.set_value(
            db, key=key, value=settings_registry.coerce_for_store(spec, value),
            actor=actor, request=request,
        )
    # Drop the middleware's cached 4xx-capture flag so the change applies at once.
    error_log_svc._reset_cache()
    return get_settings(db)
