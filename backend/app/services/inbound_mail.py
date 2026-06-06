"""Ingest a parsed inbound message into the admin inbox (v1.27.0).

Dedup by ``(uidvalidity, imap_uid)`` and ``message_id`` so re-polling is a no-op.
Attachment bytes are stored via the pluggable storage backend and ClamAV-scanned
inline (``scan_stream``; ``AV_SKIP`` short-circuits clean in dev/CI). New mail
optionally notifies admins per ``imap.notify_mode``.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import uuid

from sqlalchemy.orm import Session

from ..models.inbound_attachment import AttachmentAVState, InboundAttachment
from ..models.inbound_message import InboundMessage, MessageClass
from ..models.user import User, UserRole
from . import av_scan, imap_config
from . import storage_backend as storage_svc
from .inbound_parse import ParsedAttachment, ParsedMessage

logger = logging.getLogger("fileheron.inbound")

# Skip storing absurdly large attachments (defensive; configurable later).
_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


def _already_ingested(db: Session, *, uidvalidity: int, uid: int, message_id: str | None) -> bool:
    q = db.query(InboundMessage.id).filter(
        InboundMessage.uidvalidity == uidvalidity, InboundMessage.imap_uid == uid
    )
    if q.first() is not None:
        return True
    return bool(
        message_id
        and db.query(InboundMessage.id)
        .filter(InboundMessage.message_id == message_id)
        .first()
    )


def _store_attachment(db: Session, message_id_pk: int, att: ParsedAttachment) -> None:
    backend = storage_svc.get_storage_backend()
    locator = backend.generate_locator(f"inbound-{uuid.uuid4().hex}")
    # Scan the bytes before they land anywhere servable.
    scan = av_scan.scan_stream(io.BytesIO(att.content))
    if scan.state == "clean":
        av_state = AttachmentAVState.clean
    elif scan.state == "infected":
        av_state = AttachmentAVState.infected
    else:
        av_state = AttachmentAVState.pending
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(att.content)
            tmp_name = tmp.name
        backend.finalize(tmp_name, locator)
    except Exception:
        logger.exception("failed to store inbound attachment %s", att.filename)
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
        return
    db.add(
        InboundAttachment(
            message_id=message_id_pk,
            filename=att.filename[:255],
            content_type=(att.content_type or None),
            size_bytes=len(att.content),
            storage_key=locator,
            av_state=av_state,
        )
    )


def _notify_admins(db: Session, msg: InboundMessage) -> None:
    mode = imap_config.notify_mode(db)
    if mode == "off":
        return
    if mode == "human" and msg.classification != MessageClass.normal:
        return
    from ..models.notification import NotificationCategory
    from . import notification as notif_svc
    from . import site as site_svc

    base = site_svc.get_site_url(db)
    payload = {
        "sender": msg.sender_email,
        "subject": msg.subject,
        "classification": msg.classification.value,
    }
    link = f"{base}/admin/inbox/{msg.id}"
    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    for admin in admins:
        notif_svc.dispatch(
            db,
            user=admin,
            category=NotificationCategory.inbound_message,
            payload=payload,
            link_url=link,
        )


def ingest(
    db: Session, parsed: ParsedMessage, *, uid: int, uidvalidity: int
) -> InboundMessage | None:
    """Store one message + its attachments. Returns the row, or None if it was a
    duplicate. Caller commits."""
    if _already_ingested(db, uidvalidity=uidvalidity, uid=uid, message_id=parsed.message_id):
        return None

    sender_user_id = None
    if parsed.sender_email:
        sender_user_id = (
            db.query(User.id).filter(User.email == parsed.sender_email).scalar()
        )

    msg = InboundMessage(
        received_at=parsed.received_at,
        sender_email=(parsed.sender_email or "unknown")[:320],
        sender_name=(parsed.sender_name or None),
        sender_user_id=sender_user_id,
        to_addr=(parsed.to_addr or None),
        subject=parsed.subject[:512],
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        imap_uid=uid,
        uidvalidity=uidvalidity,
        classification=parsed.classification,
        body_text=parsed.body_text,
        body_html=parsed.body_html,
        has_attachments=bool(parsed.attachments),
    )
    db.add(msg)
    db.flush()

    for att in parsed.attachments:
        if len(att.content) > _MAX_ATTACHMENT_BYTES:
            logger.warning("skipping oversized inbound attachment %s", att.filename)
            continue
        _store_attachment(db, msg.id, att)

    _notify_admins(db, msg)
    return msg
