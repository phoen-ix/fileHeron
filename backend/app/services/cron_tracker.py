"""Per-cron-execution book-keeping decorator.

Every cron wraps its body in `@track_cron("name")`. On entry: insert a
`cron_runs` row with `status=running`. On exit: update with `success`
or `failure`, the elapsed duration, the returned dict (truncated), and
any exception message.

A run fails in one of two ways: it RAISES (re-raised, so ARQ's `max_tries`
applies), or it returns a dict carrying `CRON_FAILED_KEY` (not re-raised - see
that constant). Both go through `_record_failure`; a job that neither raises
nor sets the key is a success even if its result says otherwise.

Failures additionally:
- Record an `cron_failed` audit event with the error.
- Dispatch an `ops_alert` to every non-disabled admin via the existing
  notification rail (in-app bell), de-duplicated so repeated failures
  in the same hour don't spam.

Retention: after each successful run, prune rows older than 30 days and
keep at most 200 rows per job_name. The audit log is the durable
record; cron_runs is a sliding operator window.
"""
from __future__ import annotations

import functools
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..models.cron_run import CronRun, CronRunStatus
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..redis_client import get_redis
from ..utils.timeutil import utc_now
from . import sse as sse_svc
from .audit import record_audit_event
from .notification import dispatch

logger = logging.getLogger("fileheron.cron_tracker")

_PRUNE_AFTER_DAYS = 30
_KEEP_PER_JOB = 200
_RESULT_SUMMARY_MAX_BYTES = 4096

# Dedup window for admin notification on a single cron name's failures.
# Redis key: fh:ops:alert:cron_failed:<job_name>. TTL = this many seconds.
_DEDUP_TTL_SEC = 3600

# Opt-in key a cron may set in its returned dict to say "this run FAILED"
# without raising. Needed because a cron that catches its own errors and
# reports them in the result was recorded as a SUCCESS - it could be broken
# indefinitely while the Scheduled-tasks page stayed green (release_check was
# exactly that). Raising instead is not equivalent: the failure path below
# re-raises, and `WorkerSettings.max_tries` is 5, so a job that fails on a
# transient upstream would be re-run five times per tick and would write five
# audit rows and five `notify_admin_error` enqueues. A job that never sets this
# key is unaffected.
CRON_FAILED_KEY = "cron_failed"




def _truncate(result: Any) -> Any:
    """JSON-friendly truncation so result_summary doesn't bloat the table
    if a cron returns a large dict/list."""
    if result is None:
        return None
    try:
        import json
        encoded = json.dumps(result, default=str)
        if len(encoded) <= _RESULT_SUMMARY_MAX_BYTES:
            return result
        return {"_truncated": True, "preview": encoded[:_RESULT_SUMMARY_MAX_BYTES]}
    except Exception:
        return {"_unserializable": str(type(result))}


def _prune_old_runs(db: Session, job_name: str) -> None:
    cutoff = utc_now() - timedelta(days=_PRUNE_AFTER_DAYS)
    db.execute(
        delete(CronRun)
        .where(CronRun.job_name == job_name, CronRun.started_at < cutoff)
    )
    # Also cap rows per job - last write wins. Find the oldest started_at
    # of the rows we want to keep; delete anything older.
    rows_to_keep = (
        db.execute(
            select(CronRun.started_at)
            .where(CronRun.job_name == job_name)
            .order_by(CronRun.started_at.desc())
            .limit(_KEEP_PER_JOB)
        )
        .scalars()
        .all()
    )
    if len(rows_to_keep) >= _KEEP_PER_JOB:
        threshold = rows_to_keep[-1]
        db.execute(
            delete(CronRun)
            .where(CronRun.job_name == job_name, CronRun.started_at < threshold)
        )


def _maybe_alert_admins(db: Session, job_name: str, error_msg: str) -> None:
    """Dispatch ops_alert to admins, de-duplicated per job in a 1h window."""
    try:
        redis = get_redis()
        key = f"fh:ops:alert:cron_failed:{job_name}"
        if redis.exists(key):
            return
        redis.set(key, "1", ex=_DEDUP_TTL_SEC)
    except Exception:
        # Redis-down: dispatch anyway. Better noisy than silent.
        logger.warning("ops dedup check skipped (redis): %s", job_name, exc_info=True)

    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    if not admins:
        return
    payload = {
        "reason": "cron_failed",
        "job_name": job_name,
        "error": error_msg[:500],
        "at": utc_now().isoformat(),
    }
    for admin in admins:
        try:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/system",
                email_to=admin.email,
            )
        except Exception:
            logger.exception(
                "ops_alert dispatch to admin=%d failed", admin.id
            )

    from . import webhook as webhook_svc
    emit_payload = {"target_type": "ops", "target_id": "cron_failed", "metadata": payload}
    webhook_svc.emit_after_commit(db, webhook_svc.OPS_ALERT_EVENT, emit_payload)


def _record_failure(
    job_name: str,
    row: CronRun | None,
    duration_ms: int,
    *,
    error_msg: str,
    exception_type: str,
    traceback_text: str | None = None,
) -> None:
    """Write the `failure` outcome for one cron run: the `cron_runs` row, the
    `cron_failed` audit event, the in-app ops_alert, the email enqueue and the
    admin-page SSE nudge.

    Shared by the two ways a run can fail - an exception, and a job that
    reports failure in its result via `CRON_FAILED_KEY`. Owns its own session
    because the exception path cannot trust the caller's.
    """
    db = SessionLocal()
    try:
        if row is not None:
            live = db.query(CronRun).filter(CronRun.id == row.id).one_or_none()
            if live is not None:
                live.status = CronRunStatus.failure
                live.completed_at = utc_now()
                live.duration_ms = duration_ms
                live.error_msg = (traceback_text or error_msg)[:2000]
        record_audit_event(
            db,
            event_type=AuditEventType.cron_failed,
            actor_user_id=None,
            target_type="cron",
            target_id=job_name,
            metadata={"error": error_msg[:500], "duration_ms": duration_ms},
        )
        _maybe_alert_admins(db, job_name, error_msg)
        # Email layer (gated per-task by cron.<name>.alert_on_failure
        # + the master error-alert switch, all checked in the worker).
        # Independent of the in-app ops_alert above; best-effort.
        try:
            from . import job_queue
            job_queue.enqueue(
                "notify_admin_error",
                event={
                    "source": "worker",
                    "exception_type": exception_type,
                    "message": error_msg[:500],
                    "job_name": job_name,
                    "path": job_name,
                    "method": "CRON",
                    "status_code": 500,
                    "code": "CRON_FAILED",
                    "request_id": None,
                    "user_id": None,
                    "auth_via": None,
                    "at": utc_now().isoformat(),
                },
            )
        except Exception:
            logger.warning(
                "notify_admin_error enqueue failed for %s", job_name,
                exc_info=True,
            )
        db.commit()
        # Push to admin-system viewers so the table refreshes
        # without waiting for the manual Refresh button.
        sse_svc.publish_admin_sync({
            "event": "cron_run",
            "data": {
                "job_name": job_name,
                "status": "failure",
                "duration_ms": duration_ms,
            },
        })
    except Exception:
        db.rollback()
        logger.exception("cron_tracker: failure-path write failed for %s", job_name)
    finally:
        db.close()


def track_cron(job_name: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator. Wrap every ARQ cron entry point in
    ``@track_cron("name")`` so its run gets a row in ``cron_runs``."""

    def _decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            started = utc_now()
            t0 = time.monotonic()
            db = SessionLocal()
            row: CronRun | None = None
            try:
                row = CronRun(job_name=job_name, started_at=started, status=CronRunStatus.running)
                db.add(row)
                db.commit()
                db.refresh(row)
            except Exception:
                logger.exception("cron_tracker: could not write start row for %s", job_name)
                row = None
            finally:
                db.close()

            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                duration_ms = int((time.monotonic() - t0) * 1000)
                _record_failure(
                    job_name,
                    row,
                    duration_ms,
                    error_msg=str(exc),
                    exception_type=type(exc).__name__,
                    traceback_text=tb,
                )
                raise

            # A job that handled its own error and said so in the result. Recorded
            # as a failure, but NOT re-raised: `max_tries` would otherwise re-run
            # it, and a job that already knows it failed has nothing to retry.
            if isinstance(result, dict) and result.get(CRON_FAILED_KEY):
                duration_ms = int((time.monotonic() - t0) * 1000)
                _record_failure(
                    job_name,
                    row,
                    duration_ms,
                    error_msg=str(result.get("error") or "job reported failure"),
                    exception_type="CronReportedFailure",
                )
                return result

            # Success path.
            duration_ms = int((time.monotonic() - t0) * 1000)
            db3 = SessionLocal()
            try:
                if row is not None:
                    live = db3.query(CronRun).filter(CronRun.id == row.id).one_or_none()
                    if live is not None:
                        live.status = CronRunStatus.success
                        live.completed_at = utc_now()
                        live.duration_ms = duration_ms
                        live.result_summary = _truncate(result)
                _prune_old_runs(db3, job_name)
                db3.commit()
            except Exception:
                db3.rollback()
                logger.exception("cron_tracker: success-path write failed for %s", job_name)
            finally:
                db3.close()
            sse_svc.publish_admin_sync({
                "event": "cron_run",
                "data": {
                    "job_name": job_name,
                    "status": "success",
                    "duration_ms": duration_ms,
                },
            })
            return result

        return _wrapper

    return _decorator
