"""Outbound webhook fan-out (v1.19.0).

`emit` is the single entry point: given an event name + payload it enqueues a
delivery job per subscribed, active webhook. It is **best-effort** - it never
raises into the caller, because a webhook config problem must never break a
share / download / quarantine.

Deliberately it does NOT write a `webhook_deliveries` row: the caller's
transaction hasn't committed yet, so a row written here could be invisible (or
rolled back) when the worker runs. The worker creates + owns the delivery row
from the enqueued args instead - no cross-transaction dependency, no race.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from sqlalchemy.orm import Session

from ..models.audit_log import AuditEventType
from ..models.webhook import Webhook
from ..utils.crypto import random_token
from . import job_queue

logger = logging.getLogger("fileheron.webhook")

# Synthetic (non-audit) event for operational alerts (cron failure, low disk…).
OPS_ALERT_EVENT = "ops.alert"

# The events an admin may subscribe a webhook to. A curated subset of audit
# events (the business-meaningful ones) plus the synthetic ops alert. Surfaced
# to the UI via GET /api/admin/webhooks/events.
WEBHOOK_EVENTS: list[str] = [
    AuditEventType.share_created.value,
    AuditEventType.share_downloaded.value,
    AuditEventType.file_downloaded.value,
    AuditEventType.share_revoked.value,
    AuditEventType.share_expired.value,
    AuditEventType.file_quarantined.value,
    AuditEventType.public_link_consumed.value,
    AuditEventType.oidc_linked.value,
    AuditEventType.user_erased.value,
    AuditEventType.anomaly_detected.value,
    OPS_ALERT_EVENT,
]
_WEBHOOK_EVENTS_SET = set(WEBHOOK_EVENTS)


def is_webhook_event(event_type: str) -> bool:
    return event_type in _WEBHOOK_EVENTS_SET


def generate_secret() -> str:
    """A fresh HMAC signing secret (shown once to the admin)."""
    return random_token(32)


def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex>` - the X-Webhook-Signature header value."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _subscribed(wh: Webhook, event_type: str) -> bool:
    types = wh.event_types or []
    return "*" in types or event_type in types


def emit(db: Session, event_type: str, payload: dict) -> int:
    """Enqueue a delivery for every active webhook subscribed to `event_type`.
    Returns how many were enqueued. Never raises."""
    enqueued = 0
    try:
        webhooks = db.query(Webhook).filter(Webhook.active.is_(True)).all()
        for wh in webhooks:
            if not _subscribed(wh, event_type):
                continue
            try:
                job_queue.enqueue("webhook_deliver", wh.id, event_type, payload)
                enqueued += 1
            except Exception:
                logger.exception("webhook enqueue failed (webhook=%s)", wh.id)
    except Exception:
        # A webhook problem must never break the originating action.
        logger.exception("webhook.emit failed for event=%s", event_type)
    return enqueued
