"""Outbound webhook delivery worker (v1.19.0).

One job = one logical delivery, tracked by a single `webhook_deliveries` row.
On a transient failure the job updates the row and **self-re-enqueues** with an
increasing `_defer_by` backoff (rather than relying on ARQ's generic retry,
which would re-run with the original args and lose the row). It never raises for
HTTP/timeout failures - webhooks are best-effort.
"""
from __future__ import annotations

import json
import logging

import httpx

from ..database import SessionLocal
from ..models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus
from ..services import job_queue
from ..services import webhook as webhook_svc
from ..utils.crypto import decrypt_setting
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.webhook_deliver")

_MAX_ATTEMPTS = 5
_TIMEOUT_SEC = 5.0
# Seconds to wait before the next attempt, keyed by the attempt just completed.
_BACKOFF = {1: 5, 2: 15, 3: 30, 4: 60}


def _body_bytes(event_type: str, delivery_id: int, payload: dict) -> bytes:
    body = {
        "event": event_type,
        "occurred_at": utc_now().isoformat(),
        "delivery_id": delivery_id,
        "actor_user_id": payload.get("actor_user_id"),
        "target_type": payload.get("target_type"),
        "target_id": payload.get("target_id"),
        "data": payload.get("metadata"),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def webhook_deliver(
    _ctx,
    webhook_id: int,
    event_type: str,
    payload: dict,
    delivery_id: int | None = None,
    attempt: int = 1,
) -> dict:
    db = SessionLocal()
    try:
        wh = db.query(Webhook).filter(Webhook.id == webhook_id).one_or_none()
        if wh is None or not wh.active:
            return {"skipped": True, "reason": "webhook missing or inactive"}

        if delivery_id is not None:
            delivery = (
                db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).one_or_none()
            )
            if delivery is None:
                return {"skipped": True, "reason": "delivery row gone"}
        else:
            delivery = WebhookDelivery(
                webhook_id=wh.id,
                event_type=event_type,
                payload=payload,
                status=WebhookDeliveryStatus.pending,
            )
            db.add(delivery)
            db.flush()

        body = _body_bytes(event_type, delivery.id, payload)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "fileHeron-webhook/1",
            "X-Webhook-Id": str(wh.id),
            "X-Webhook-Event": event_type,
            "X-Webhook-Delivery": str(delivery.id),
            "X-Webhook-Signature": webhook_svc.sign(decrypt_setting(wh.secret_encrypted), body),
        }

        delivery.attempts = attempt
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
                resp = await client.post(wh.url, content=body, headers=headers)
            delivery.response_code = resp.status_code
            ok = 200 <= resp.status_code < 300
            err = None if ok else f"HTTP {resp.status_code}"
        except Exception as e:  # network / timeout / DNS
            delivery.response_code = None
            ok = False
            err = str(e)[:500]

        if ok:
            delivery.status = WebhookDeliveryStatus.sent
            delivery.delivered_at = utc_now()
            delivery.error = None
            db.commit()
            return {"delivery_id": delivery.id, "status": "sent"}

        delivery.error = err
        if attempt < _MAX_ATTEMPTS:
            delivery.status = WebhookDeliveryStatus.pending
            db.commit()
            try:
                await job_queue.aenqueue(
                    "webhook_deliver",
                    webhook_id,
                    event_type,
                    payload,
                    delivery_id=delivery.id,
                    attempt=attempt + 1,
                    _defer_by=_BACKOFF.get(attempt, 60),
                )
            except Exception:
                logger.exception("webhook retry re-enqueue failed (delivery=%s)", delivery.id)
            return {"delivery_id": delivery.id, "status": "retry", "attempt": attempt}

        delivery.status = WebhookDeliveryStatus.failed
        delivery.delivered_at = utc_now()
        db.commit()
        return {"delivery_id": delivery.id, "status": "failed"}
    finally:
        db.close()
