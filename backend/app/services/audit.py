"""Single entry point for audit-log writes. Every privileged or
security-relevant action calls `record_audit_event(...)`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType, AuditLog
from ..utils.columns import declared_width

# Widths derived from the columns, never literals: a clip to the WRONG width is
# the same failure with a longer fuse (that is exactly what `target_id`'s
# clip-to-255 against a String(64) was, 027fe08). `request.client.host` is the
# address uvicorn resolved from the leftmost X-Forwarded-For, so on an edge that
# appends rather than overwrites it is caller-controlled - and this function is
# the single funnel for EVERY audited action, so an over-long value would fail
# the write it is recording. `request_id` is minted internally and fits, but it
# costs nothing to bound it the same way.
_IP_MAX = declared_width(AuditLog.__table__.c.ip)
_REQUEST_ID_MAX = declared_width(AuditLog.__table__.c.request_id)

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
        if isinstance(request_id, str):
            request_id = request_id[:_REQUEST_ID_MAX]
        if request.client:
            ip = request.client.host[:_IP_MAX]

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
    # raises). Deferred to AFTER the caller's commit so a post-audit rollback
    # can't deliver a ghost event for a change that never persisted - and run
    # from a session of its own, because the after-commit hook cannot emit SQL
    # on the originating one. Lazy import - webhook -> models -> audit would
    # cycle at import.
    try:
        from . import webhook as webhook_svc

        if webhook_svc.is_webhook_event(et):
            payload = {
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "target_id": str(target_id) if target_id is not None else None,
                "metadata": metadata,
            }
            webhook_svc.emit_after_commit(db, et, payload)
    except Exception:
        logger.exception("webhook emit from audit failed for event=%s", et)

    return row
