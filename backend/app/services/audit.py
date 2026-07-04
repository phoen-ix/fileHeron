"""Single entry point for audit-log writes. Every privileged or
security-relevant action calls `record_audit_event(...)`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType, AuditLog

logger = logging.getLogger("fileheron.audit")


def record_audit_event(
    db: Session,
    *,
    event_type: AuditEventType | str,
    actor_user_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Insert an audit log row. Caller commits."""
    et = event_type.value if isinstance(event_type, AuditEventType) else event_type
    request_id: str | None = None
    ip: str | None = None
    if request is not None:
        request_id = getattr(request.state, "request_id", None)
        if request.client:
            ip = request.client.host

    row = AuditLog(
        actor_user_id=actor_user_id,
        event_type=et,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        request_id=request_id,
        ip=ip,
        extra=metadata,
    )
    db.add(row)
    db.flush()
    logger.info(
        "audit",
        extra={
            "event_type": et,
            "actor_user_id": actor_user_id,
            "target_type": target_type,
            "target_id": target_id,
            "request_id": request_id,
            "ip": ip,
        },
    )

    # Fan out to outbound webhooks subscribed to this event (best-effort, never
    # raises). Deferred to AFTER the caller's commit (run_after_commit) so a
    # post-audit rollback can't deliver a ghost event for a change that never
    # persisted. Lazy import - webhook → models → audit would cycle at import.
    try:
        from ..database import run_after_commit
        from . import webhook as webhook_svc

        if webhook_svc.is_webhook_event(et):
            payload = {
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "target_id": str(target_id) if target_id is not None else None,
                "metadata": metadata,
            }
            run_after_commit(db, lambda: webhook_svc.emit(db, et, payload))
    except Exception:
        logger.exception("webhook emit from audit failed for event=%s", et)

    return row
