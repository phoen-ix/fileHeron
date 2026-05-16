"""ARQ worker — send a pre-rendered email.

The notification dispatcher renders subject + text + html on the
producing side (where the user's locale and the template payload are
trivially available) and enqueues only the rendered strings. The worker
just talks SMTP.

Retry policy: ARQ's `Retry` exception with `defer` lets us back off
gracefully. We retry 3 times on transient SMTP errors (connection
refused, 4xx codes); permanent failures (5xx) log + give up so the
worker doesn't loop forever.
"""
from __future__ import annotations

import logging

from aiosmtplib.errors import (
    SMTPConnectError,
    SMTPResponseException,
    SMTPServerDisconnected,
    SMTPTimeoutError,
)
from arq import Retry

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..services.audit import record_audit_event
from ..services.email import resolve_smtp_config
from ..utils.emailing import send_email

logger = logging.getLogger("fileheron.workers.send_email")

_TRANSIENT_ERRORS = (
    SMTPConnectError,
    SMTPServerDisconnected,
    SMTPTimeoutError,
    OSError,  # network blip
)


async def send_email_job(
    ctx, *, to: str, subject: str, text_body: str, html_body: str | None = None
) -> dict:
    """Worker entry point. Returns a small status dict for diagnostics.

    Resolves SMTP config per-job so admin-saved settings take effect
    on the next send without a worker restart.
    """
    db = SessionLocal()
    try:
        cfg = resolve_smtp_config(db)
    finally:
        db.close()
    try:
        await send_email(
            cfg=cfg, to=to, subject=subject, text_body=text_body, html_body=html_body
        )
        return {"to": to, "subject": subject, "status": "sent"}
    except _TRANSIENT_ERRORS as e:
        attempt = ctx.get("job_try", 1)
        # 1s, 5s, 30s — exponential-ish.
        defer = (1, 5, 30)[min(attempt - 1, 2)]
        logger.warning(
            "transient SMTP error sending to %s (attempt %d/3): %s — retrying in %ds",
            to,
            attempt,
            e,
            defer,
        )
        raise Retry(defer=defer) from e
    except SMTPResponseException as e:
        # 4xx is technically transient; 5xx permanent. Treat 4xx like the
        # transient block above; 5xx → log and give up (return success
        # so ARQ doesn't keep retrying — the job's outcome is "we tried").
        if 400 <= e.code < 500:
            attempt = ctx.get("job_try", 1)
            defer = (1, 5, 30)[min(attempt - 1, 2)]
            logger.warning(
                "SMTP 4xx (%s) sending to %s (attempt %d/3): %s — retrying in %ds",
                e.code,
                to,
                attempt,
                e.message,
                defer,
            )
            raise Retry(defer=defer) from e
        logger.error(
            "permanent SMTP failure (%s) sending to %s: %s — giving up",
            e.code,
            to,
            e.message,
        )
        # Audit the undeliverable so ops_check (hourly) surfaces it to
        # admins instead of failing silently. Best-effort: never let the
        # audit write swallow the worker's outcome dict.
        try:
            audit_db = SessionLocal()
            try:
                record_audit_event(
                    audit_db,
                    event_type=AuditEventType.email_undeliverable,
                    actor_user_id=None,
                    target_type="email",
                    target_id=to,
                    metadata={
                        "subject": subject[:120],
                        "smtp_code": e.code,
                        "smtp_message": (e.message or "")[:300],
                    },
                )
                audit_db.commit()
            finally:
                audit_db.close()
        except Exception:
            logger.exception("could not record email_undeliverable audit event for %s", to)
        return {"to": to, "subject": subject, "status": "failed", "code": e.code}
    except Exception:
        logger.exception("unexpected error sending email to %s", to)
        return {"to": to, "subject": subject, "status": "error"}
