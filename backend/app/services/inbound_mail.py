"""Ingest a parsed inbound message into the admin inbox (v1.27.0).

Dedup by ``(uidvalidity, imap_uid)`` - server-assigned, so re-polling is a
no-op. ``message_id`` is stored but deliberately does NOT decide: it is an
attacker-controlled header, and letting it decide meant a forged value could
silently delete a later genuine mail (audit #2, N-15).
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
# Attachments stored per message. Bounds the fan-out of file writes, clamd
# sessions and DB rows a single mail can trigger; see the note at the call site.
MAX_ATTACHMENTS_PER_MESSAGE = 50


def _already_ingested(db: Session, *, uidvalidity: int, uid: int, message_id: str | None) -> bool:
    """Whether this mailbox slot has already been ingested.

    Keyed on `(uidvalidity, imap_uid)` ONLY. That pair is assigned by the IMAP
    server, is stable for the life of the mailbox, and is what actually makes
    re-polling idempotent.

    `message_id` used to be a second, independent key here: any prior row with
    the same Message-ID meant "already ingested", regardless of UID. But
    Message-ID comes straight off the wire (`inbound_parse` reads the header
    verbatim) and is trivially forgeable, so a sender who knew or guessed an
    already-ingested value could make a later genuine mail be treated as a
    duplicate - and the poll then advances its UID highwater past it, so it is
    never reconsidered. Dropped with a log line, no row, nothing admin-visible.
    Non-adversarially the same thing happened by accident whenever a bulk
    sender, mailing list or forwarding loop reused an ID (audit #2, N-15).

    An attacker-controlled header must not be able to silently delete mail, so
    it no longer decides. It is still STORED, and `message_id_seen_before`
    below lets callers surface a collision instead of acting on it."""
    return (
        db.query(InboundMessage.id)
        .filter(
            InboundMessage.uidvalidity == uidvalidity,
            InboundMessage.imap_uid == uid,
        )
        .first()
        is not None
    )


def message_id_seen_before(db: Session, *, message_id: str | None) -> bool:
    """Whether this Message-ID has been ingested before, under any UID.

    Advisory only - a caller may record or surface the collision, but must not
    drop the message on the strength of it. See `_already_ingested`."""
    if not message_id:
        return False
    return (
        db.query(InboundMessage.id)
        .filter(InboundMessage.message_id == message_id)
        .first()
        is not None
    )


def ingested_by_uid(db: Session, *, uidvalidity: int, uid: int) -> bool:
    """True if THIS exact (uidvalidity, imap_uid) was already ingested - a genuine
    re-poll of the same server message (safe to re-apply the server-side action),
    as distinct from a Message-ID collision with a DIFFERENT message (which must
    NOT be deleted/moved, or a legitimate distinct mail is destroyed unread)."""
    return (
        db.query(InboundMessage.id)
        .filter(InboundMessage.uidvalidity == uidvalidity, InboundMessage.imap_uid == uid)
        .first()
        is not None
    )


def sender_is_accepted(db: Session, raw_headers: bytes | None) -> bool:
    """Whether this instance will accept mail with these headers.

    Decided from the HEADERS alone, so an unknown sender's body is never
    downloaded - refusing after the fetch spends the resource the gate exists to
    protect. `None` (the server declined the header fetch, or an older test
    double) means "cannot tell": accept, and let `ingest` decide on the full
    message, which is where the authoritative check still lives.
    """
    if raw_headers is None:
        return True
    if not imap_config.require_known_sender(db):
        return True
    try:
        from . import inbound_parse

        parsed = inbound_parse.parse(raw_headers)
    except Exception:
        logger.warning("inbound: could not parse headers for the sender pre-check")
        return True
    if not parsed.sender_email:
        return False
    known = (
        db.query(User.id)
        .filter(User.email == parsed.sender_email, User.is_disabled.is_(False))
        .scalar()
    )
    if known is None:
        logger.info(
            "inbound: refusing mail from unknown sender %r before fetching the body "
            "(imap.require_known_sender)",
            parsed.sender_email,
        )
        return False
    return True


class UnknownSenderError(Exception):
    """The From address matches no enabled user and `imap.require_known_sender`
    is on. Raised before anything is stored so the poll can skip the message and
    leave it on the server."""


def _store_attachment(db: Session, message_id_pk: int, att: ParsedAttachment) -> bool:
    """Store one attachment. Returns False if the bytes could not be persisted.

    The caller must act on that: a storage failure used to be swallowed with a
    bare `return`, the message was committed with `has_attachments=True` and no
    attachment row, and `post_fetch_action=delete` then expunged the mail from
    the server - so a client's contract existed nowhere at all, and the admin
    saw an inbox entry claiming attachments and listing none (audit #2).
    """
    backend = storage_svc.get_storage_backend()
    locator = backend.generate_locator(f"inbound-{uuid.uuid4().hex}")
    # Scan the bytes before they land anywhere servable. If clamd is
    # unavailable (its documented slow first boot, or any outage), store the
    # attachment as `pending` (gated from download) and carry on - letting the
    # AVUnavailableError propagate would abort the whole poll run, the
    # highwater would never advance, and ALL inbound ingestion would silently
    # stall until clamd recovered (audit M10).
    try:
        scan = av_scan.scan_stream(io.BytesIO(att.content))
    except av_scan.AVUnavailableError:
        logger.warning(
            "clamd unavailable scanning inbound attachment %s; storing as pending",
            att.filename,
        )
        scan = None
    if scan is None or scan.state not in ("clean", "infected"):
        av_state = AttachmentAVState.pending
    elif scan.state == "clean":
        av_state = AttachmentAVState.clean
    else:
        av_state = AttachmentAVState.infected
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
        return False
    # The bytes are on the storage backend already and the commit belongs to
    # run_poll, several layers up. If that commit never lands, the blob is
    # orphaned: no InboundAttachment row references it, and every sweeper in the
    # codebase works from DB rows, so nothing can ever find it again
    # (audit 2026-07-30). Compensate on rollback.
    from ..database import run_after_rollback

    def _drop_orphan(loc: str = locator) -> None:
        try:
            backend.delete(loc)
        except Exception:
            logger.warning("inbound: could not drop orphaned attachment blob %s", loc)

    run_after_rollback(db, _drop_orphan)
    db.add(
        InboundAttachment(
            message_id=message_id_pk,
            filename=att.filename[:255],
            content_type=(att.content_type or None) and att.content_type[:127],
            size_bytes=len(att.content),
            storage_key=locator,
            av_state=av_state,
        )
    )
    return True


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
            email_to=admin.email,
        )


def ingest(
    db: Session, parsed: ParsedMessage, *, uid: int, uidvalidity: int
) -> InboundMessage | None:
    """Store one message + its attachments. Returns the row, or None if it was a
    duplicate. Caller commits."""
    # A Message-ID collision with a DIFFERENT server message is not a duplicate
    # - it is a distinct mail that will be silently dropped while the poll's UID
    # highwater advances past it, so it is never seen again and nothing records
    # that it existed. Message-IDs are client-generated and not guaranteed
    # unique; a misconfigured sender can reuse one across genuinely different
    # mails. Log it loudly so an operator can see the reuse (audit 2026-07-30).
    if parsed.message_id and not ingested_by_uid(
        db, uidvalidity=uidvalidity, uid=uid
    ) and message_id_seen_before(db, message_id=parsed.message_id):
        # Recorded, NOT acted on. This used to drop the message, which handed a
        # forgeable header the power to delete mail silently; it is now advisory
        # (audit #2, N-15). A duplicate row is recoverable, a missing one is not.
        logger.warning(
            "inbound: uid=%s (uidvalidity=%s) reuses Message-ID %r, which has "
            "been ingested before. Both are kept - a repeated Message-ID is "
            "normal for bulk senders and forwarding loops, and is forgeable, so "
            "it does not decide whether mail is dropped.",
            uid, uidvalidity, parsed.message_id,
        )
    if _already_ingested(db, uidvalidity=uidvalidity, uid=uid, message_id=parsed.message_id):
        return None

    sender_user_id = None
    if parsed.sender_email:
        sender_user_id = (
            db.query(User.id)
            .filter(User.email == parsed.sender_email, User.is_disabled.is_(False))
            .scalar()
        )
    if sender_user_id is None and imap_config.require_known_sender(db):
        # Refused BEFORE anything is stored, and the caller leaves the mail on
        # the server: nothing is written to the storage backend for a sender
        # this instance does not know. A From header is forgeable, so this is a
        # resource gate, not an authentication decision - the impersonation half
        # is handled by only binding `sender_user_id` for a sender that matches
        # a real, enabled account.
        logger.info(
            "inbound: refusing uid=%s from unknown sender %r "
            "(imap.require_known_sender)",
            uid, parsed.sender_email,
        )
        raise UnknownSenderError(parsed.sender_email or "")

    # Truncate every String-column field to its length: an over-long header
    # (spam/malformed) otherwise raises DataError on commit under MariaDB strict
    # mode, which aborts the whole poll and never advances the UID highwater, so
    # the same message re-wedges every subsequent poll.
    msg = InboundMessage(
        received_at=parsed.received_at,
        sender_email=(parsed.sender_email or "unknown")[:320],
        sender_name=(parsed.sender_name or None) and parsed.sender_name[:255],
        sender_user_id=sender_user_id,
        to_addr=(parsed.to_addr or None) and parsed.to_addr[:320],
        subject=parsed.subject[:512],
        message_id=(parsed.message_id or None) and parsed.message_id[:320],
        in_reply_to=(parsed.in_reply_to or None) and parsed.in_reply_to[:320],
        imap_uid=uid,
        uidvalidity=uidvalidity,
        classification=parsed.classification,
        body_text=parsed.body_text,
        body_html=parsed.body_html,
        has_attachments=False,  # set below from what was actually stored
    )
    db.add(msg)
    db.flush()

    stored = 0
    dropped: list[str] = []
    for att in parsed.attachments[:MAX_ATTACHMENTS_PER_MESSAGE]:
        if len(att.content) > _MAX_ATTACHMENT_BYTES:
            # Recorded in the message body, not only in a log line the admin
            # will never see: with post_fetch_action=delete the mail is gone
            # from the server too, so this note is the only evidence that a
            # file arrived at all (audit #2).
            dropped.append(f"{att.filename} ({len(att.content)} bytes, over the limit)")
            logger.warning("skipping oversized inbound attachment %s", att.filename)
            continue
        if _store_attachment(db, msg.id, att):
            stored += 1
        else:
            dropped.append(f"{att.filename} (could not be stored)")
            msg._fh_incomplete = True
    if len(parsed.attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        # A 16 MB mail can declare ~171,000 minimal parts; one file, one clamd
        # session and one row each would run for hours and exhaust the volume's
        # inodes, and every guard before this one passes because each part is a
        # single byte (audit #2).
        extra = len(parsed.attachments) - MAX_ATTACHMENTS_PER_MESSAGE
        dropped.append(f"{extra} further attachments (over the per-message limit)")
        msg._fh_incomplete = True
        logger.warning(
            "inbound uid=%s declares %d attachments; stored the first %d",
            uid, len(parsed.attachments), MAX_ATTACHMENTS_PER_MESSAGE,
        )

    # `has_attachments` used to be set from the PARSED message, so a paperclip
    # appeared for attachments that had been dropped or had failed to store.
    msg.has_attachments = stored > 0
    if dropped:
        note = "[fileHeron] attachments not stored: " + "; ".join(dropped)
        msg.body_text = f"{note}\n\n{msg.body_text or ''}"

    _notify_admins(db, msg)
    return msg
