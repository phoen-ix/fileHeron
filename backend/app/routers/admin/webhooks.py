"""/api/admin/webhooks - manage outbound webhook subscriptions + deliveries.

Admin-only. The signing secret is generated server-side, stored Fernet-encrypted,
and returned exactly once (on create / rotate); thereafter only `secret_set` is
exposed. See services/webhook.py + workers/webhook_deliver.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.user import User
from ...models.webhook import Webhook, WebhookDelivery
from ...schemas.webhook import (
    CreateWebhookRequest,
    UpdateWebhookRequest,
    WebhookCreateResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
)
from ...services import job_queue
from ...services import webhook as webhook_svc
from ...services.audit import record_audit_event
from ...utils.crypto import encrypt_setting
from ...utils.timeutil import utc_now

router = APIRouter()


def _to_response(wh: Webhook) -> WebhookResponse:
    return WebhookResponse(
        id=wh.id,
        name=wh.name,
        url=wh.url,
        event_types=wh.event_types or [],
        active=wh.active,
        secret_set=bool(wh.secret_encrypted),
        created_at=wh.created_at,
    )


def _get_or_404(db: Session, webhook_id: int) -> Webhook:
    wh = db.query(Webhook).filter(Webhook.id == webhook_id).one_or_none()
    if wh is None:
        raise AppError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
    return wh


@router.get("/webhooks/events")
def list_webhook_events(_admin: User = Depends(get_current_admin)) -> dict:
    """The events an admin may subscribe to (drives the create-form checkboxes)."""
    return {"events": webhook_svc.WEBHOOK_EVENTS}


@router.get("/webhooks")
def list_webhooks(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> list[WebhookResponse]:
    rows = db.query(Webhook).order_by(Webhook.created_at.desc()).all()
    return [_to_response(w) for w in rows]


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: CreateWebhookRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> WebhookCreateResponse:
    secret = webhook_svc.generate_secret()
    wh = Webhook(
        name=payload.name.strip(),
        url=payload.url.strip(),
        secret_encrypted=encrypt_setting(secret),
        event_types=payload.event_types,
        active=True,
        created_by_id=admin.id,
    )
    db.add(wh)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.webhook_created,
        actor_user_id=admin.id,
        target_type="webhook",
        target_id=wh.id,
        metadata={"events": payload.event_types},
        request=request,
    )
    db.commit()
    base = _to_response(wh)
    return WebhookCreateResponse(**base.model_dump(), secret=secret)


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: int,
    payload: UpdateWebhookRequest,
    request: Request,
    rotate_secret: bool = Query(False),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> WebhookResponse | WebhookCreateResponse:
    wh = _get_or_404(db, webhook_id)
    if payload.name is not None:
        wh.name = payload.name.strip()
    if payload.url is not None:
        wh.url = payload.url.strip()
    if payload.event_types is not None:
        wh.event_types = payload.event_types
    if payload.active is not None:
        wh.active = payload.active
    new_secret = None
    if rotate_secret:
        new_secret = webhook_svc.generate_secret()
        wh.secret_encrypted = encrypt_setting(new_secret)
    wh.updated_at = utc_now()
    record_audit_event(
        db,
        event_type=AuditEventType.webhook_updated,
        actor_user_id=admin.id,
        target_type="webhook",
        target_id=wh.id,
        metadata={"rotated_secret": rotate_secret},
        request=request,
    )
    db.commit()
    base = _to_response(wh)
    if new_secret is not None:
        return WebhookCreateResponse(**base.model_dump(), secret=new_secret)
    return base


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    webhook_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    wh = _get_or_404(db, webhook_id)
    record_audit_event(
        db,
        event_type=AuditEventType.webhook_deleted,
        actor_user_id=admin.id,
        target_type="webhook",
        target_id=wh.id,
        metadata={"name": wh.name},
        request=request,
    )
    db.delete(wh)
    db.commit()


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    wh = _get_or_404(db, webhook_id)
    job_queue.enqueue(
        "webhook_deliver",
        wh.id,
        "webhook.ping",
        {"target_type": "webhook", "target_id": str(wh.id), "metadata": {"ping": True}},
    )
    return {"queued": True}


@router.get("/webhooks/{webhook_id}/deliveries")
def list_deliveries(
    webhook_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> list[WebhookDeliveryResponse]:
    _get_or_404(db, webhook_id)
    rows = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        WebhookDeliveryResponse(
            id=r.id,
            event_type=r.event_type,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            response_code=r.response_code,
            attempts=r.attempts,
            error=r.error,
            created_at=r.created_at,
            delivered_at=r.delivered_at,
        )
        for r in rows
    ]


@router.post("/webhook-deliveries/{delivery_id}/retry")
def retry_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    delivery = (
        db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id).one_or_none()
    )
    if delivery is None:
        raise AppError(404, "DELIVERY_NOT_FOUND", "Delivery not found.")
    job_queue.enqueue(
        "webhook_deliver", delivery.webhook_id, delivery.event_type, delivery.payload
    )
    return {"queued": True}
