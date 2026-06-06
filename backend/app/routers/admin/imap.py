"""/api/admin — inbound mailbox: IMAP settings + the admin inbox (v1.27.0).

Settings mirror the SMTP page (password null=keep/""=clear/other=replace, never
echoed). The inbox list/detail mirror the mail-log (deferred bodies on the list,
loaded only on detail). Attachment download is gated on a clean AV scan.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.inbound_attachment import AttachmentAVState, InboundAttachment
from ...models.inbound_message import InboundMessage, MessageStatus
from ...models.user import User
from ...schemas.imap_settings import (
    ImapFetchNowResponse,
    ImapSettingsResponse,
    ImapTestResponse,
    InboxAttachmentItem,
    InboxDetail,
    InboxListItem,
    InboxListResponse,
    UpdateImapSettingsRequest,
    UpdateInboxStatusRequest,
)
from ...services import imap_config
from ...services import imap_poll as imap_poll_svc
from ...services import settings as settings_svc
from ...services import settings_registry as _sr
from ...services import storage_backend as storage_svc
from ...services.audit import record_audit_event

router = APIRouter()
K = settings_svc.Keys


# ---- Settings --------------------------------------------------------------


def _settings_response(db: Session) -> ImapSettingsResponse:
    cfg = imap_config.resolve_imap_config(db)
    return ImapSettingsResponse(
        enabled=imap_config.is_enabled(db),
        check_mode=imap_config.check_mode(db),
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        is_password_set=bool(settings_svc.get(db, K.IMAP_PASSWORD)),
        tls_mode=cfg.tls_mode,
        mailbox=cfg.mailbox,
        post_fetch_action=imap_config.post_fetch_action(db),
        move_folder=imap_config.move_folder(db),
        notify_mode=imap_config.notify_mode(db),
        poll_interval_minutes=int(_sr.effective(db, K.IMAP_POLL_INTERVAL_MINUTES)),
        last_poll_at=settings_svc.get(db, K.IMAP_LAST_POLL_AT),
        last_success_at=settings_svc.get(db, K.IMAP_LAST_SUCCESS_AT),
    )


@router.get("/settings/imap", response_model=ImapSettingsResponse)
def get_imap_settings(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> ImapSettingsResponse:
    return _settings_response(db)


@router.put("/settings/imap", response_model=ImapSettingsResponse)
def update_imap_settings(
    payload: UpdateImapSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ImapSettingsResponse:
    pairs: list[tuple[str, str | None]] = [
        (K.IMAP_ENABLED, "true" if payload.enabled else "false"),
        (K.IMAP_CHECK_MODE, payload.check_mode),
        (K.IMAP_HOST, payload.host or None),
        (K.IMAP_PORT, str(payload.port)),
        (K.IMAP_USER, payload.user or None),
        (K.IMAP_TLS_MODE, payload.tls_mode),
        (K.IMAP_MAILBOX, payload.mailbox or "INBOX"),
        (K.IMAP_POST_FETCH_ACTION, payload.post_fetch_action),
        (K.IMAP_MOVE_FOLDER, payload.move_folder or None),
        (K.IMAP_NOTIFY_MODE, payload.notify_mode),
        (K.IMAP_POLL_INTERVAL_MINUTES, str(payload.poll_interval_minutes)),
    ]
    for key, value in pairs:
        settings_svc.set_value(db, key=key, value=value, actor=admin, request=request)
    if payload.password is not None:
        settings_svc.set_value(
            db, key=K.IMAP_PASSWORD,
            value=payload.password if payload.password else None,
            actor=admin, request=request,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.imap_config_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="imap",
        metadata={"enabled": payload.enabled, "check_mode": payload.check_mode,
                  "post_fetch_action": payload.post_fetch_action,
                  "notify_mode": payload.notify_mode},
        request=request,
    )
    db.commit()
    return _settings_response(db)


@router.post("/settings/imap/test", response_model=ImapTestResponse)
async def test_imap(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> ImapTestResponse:
    result = await asyncio.to_thread(imap_poll_svc.test_connection, db)
    return ImapTestResponse(**result)


@router.post("/settings/imap/fetch-now", response_model=ImapFetchNowResponse)
async def fetch_now(
    _admin: User = Depends(get_current_admin),
) -> ImapFetchNowResponse:
    # run_poll opens its own session (it runs in a worker thread).
    result = await asyncio.to_thread(imap_poll_svc.run_poll, manual=True)
    return ImapFetchNowResponse(
        ok=bool(result.get("ok")),
        skipped=result.get("skipped"),
        error=result.get("error"),
        fetched=result.get("fetched"),
        ingested=result.get("ingested"),
    )


# ---- Inbox -----------------------------------------------------------------


def _list_item(m: InboundMessage) -> InboxListItem:
    return InboxListItem(
        id=m.id, created_at=m.created_at, received_at=m.received_at,
        sender_email=m.sender_email, sender_name=m.sender_name,
        sender_user_id=m.sender_user_id, subject=m.subject,
        classification=m.classification.value, status=m.status.value,
        has_attachments=m.has_attachments,
    )


@router.get("/inbox", response_model=InboxListResponse)
def list_inbox(
    q: str | None = Query(None),
    classification: str | None = Query(None),
    status: str | None = Query(None),
    sender_email: str | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> InboxListResponse:
    base = db.query(InboundMessage)
    if q:
        like = f"%{q}%"
        base = base.filter(or_(InboundMessage.subject.ilike(like),
                               InboundMessage.sender_email.ilike(like)))
    if classification:
        base = base.filter(InboundMessage.classification == classification)
    if status:
        base = base.filter(InboundMessage.status == status)
    if sender_email:
        base = base.filter(InboundMessage.sender_email.ilike(f"%{sender_email}%"))
    total = base.count()
    unread = db.query(InboundMessage).filter(
        InboundMessage.status == MessageStatus.new
    ).count()
    rows = (
        base.order_by(InboundMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return InboxListResponse(
        items=[_list_item(m) for m in rows],
        total=total, page=page, page_size=page_size, unread=unread,
    )


@router.get("/inbox/unread-count")
def inbox_unread_count(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> dict:
    n = db.query(InboundMessage).filter(InboundMessage.status == MessageStatus.new).count()
    return {"unread": n}


def _get_message_or_404(db: Session, msg_id: int) -> InboundMessage:
    m = db.query(InboundMessage).filter(InboundMessage.id == msg_id).one_or_none()
    if m is None:
        raise AppError(404, "MESSAGE_NOT_FOUND", "Inbound message not found.")
    return m


@router.get("/inbox/{msg_id}", response_model=InboxDetail)
def get_inbox_message(
    msg_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> InboxDetail:
    m = _get_message_or_404(db, msg_id)
    atts = db.query(InboundAttachment).filter(
        InboundAttachment.message_id == m.id
    ).all()
    base = _list_item(m)
    return InboxDetail(
        **base.model_dump(),
        to_addr=m.to_addr, message_id=m.message_id, in_reply_to=m.in_reply_to,
        body_text=m.body_text, body_html=m.body_html,
        attachments=[
            InboxAttachmentItem(
                id=a.id, filename=a.filename, content_type=a.content_type,
                size_bytes=a.size_bytes, av_state=a.av_state.value,
            )
            for a in atts
        ],
    )


@router.patch("/inbox/{msg_id}", response_model=InboxDetail)
def update_inbox_status(
    msg_id: int,
    payload: UpdateInboxStatusRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> InboxDetail:
    m = _get_message_or_404(db, msg_id)
    m.status = MessageStatus(payload.status)
    db.commit()
    return get_inbox_message(msg_id, db=db, _admin=_admin)


@router.delete("/inbox/{msg_id}", status_code=204)
def delete_inbox_message(
    msg_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> None:
    m = _get_message_or_404(db, msg_id)
    backend = storage_svc.get_storage_backend()
    for a in db.query(InboundAttachment).filter(InboundAttachment.message_id == m.id).all():
        try:
            backend.delete(a.storage_key)
        except Exception:
            pass
    db.delete(m)
    db.commit()


@router.get("/inbox/{msg_id}/attachments/{att_id}/download")
def download_inbox_attachment(
    msg_id: int,
    att_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    att = (
        db.query(InboundAttachment)
        .filter(InboundAttachment.id == att_id, InboundAttachment.message_id == msg_id)
        .one_or_none()
    )
    if att is None:
        raise AppError(404, "ATTACHMENT_NOT_FOUND", "Attachment not found.")
    if att.av_state != AttachmentAVState.clean:
        raise AppError(409, "ATTACHMENT_NOT_CLEAN", "Attachment is not available for download.")
    backend = storage_svc.get_storage_backend()
    return storage_svc.serve_response(
        backend,
        locator=att.storage_key,
        filename=att.filename,
        mime_type=att.content_type or "application/octet-stream",
        ttl_sec=300,
        disposition="attachment",
        extra_headers={"X-Content-Type-Options": "nosniff"},
    )
