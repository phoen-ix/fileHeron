"""Parse a raw RFC822 message into the fields the inbox stores (v1.27.0).

Pure (no DB/IMAP) so it's testable on raw bytes. HTML is sanitised with nh3 here
so a poisoned body can never reach the DB un-sanitised; the detail view renders
it in a sandboxed iframe as a second layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from ..models.inbound_message import MessageClass
from . import email as email_svc  # reuse the nh3 _sanitize_html helper
from . import inbound_classify

_MAX_BODY = 1_000_000  # 1 MB cap per body part (defensive)
# ...and a TOTAL cap across parts. Per-part alone was not a bound: a 20 MB mail
# of twenty 1 MB text parts assembled a 20 MB body_text, whose INSERT exceeds
# MariaDB's default max_allowed_packet (16 MB). The server answers 1153 and
# drops the connection, the per-message handler swallows it and advances the
# highwater, so the mail is gone from fileHeron's point of view while still
# sitting unread on the server with no record that it arrived (audit #2).
_MAX_BODY_TOTAL = 4_000_000


def _join_capped(parts: list[str]) -> str | None:
    """Join body parts, stopping at the total cap and saying so in the text."""
    out: list[str] = []
    used = 0
    for part in parts:
        if used + len(part) > _MAX_BODY_TOTAL:
            out.append(part[: max(0, _MAX_BODY_TOTAL - used)])
            out.append("\n[fileHeron] message body truncated at the size limit.")
            break
        out.append(part)
        used += len(part)
    return "\n".join(out).strip() or None


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str | None
    content: bytes


@dataclass
class ParsedMessage:
    sender_email: str
    sender_name: str | None
    to_addr: str | None
    subject: str
    message_id: str | None
    in_reply_to: str | None
    received_at: datetime | None
    classification: MessageClass
    body_text: str | None
    body_html: str | None  # already nh3-sanitised
    attachments: list[ParsedAttachment] = field(default_factory=list)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse(raw: bytes) -> ParsedMessage:
    msg: Message = message_from_bytes(raw)

    name, addr = parseaddr(msg.get("From") or "")
    subject = _decode(msg.get("Subject")) or "(no subject)"
    try:
        received = _naive_utc(parsedate_to_datetime(msg.get("Date"))) if msg.get("Date") else None
    except Exception:
        # Deliberately broad. A crafted Date raises OverflowError ("signed
        # integer is greater than maximum") for an absurd year or offset, which
        # escaped the old (TypeError, ValueError) guard - and an exception here
        # aborts the whole poll before the UID highwater advances, so ONE such
        # message wedges all inbound mail forever (audit 2026-07-30). No Date
        # header is worth that; an unparseable one is simply unknown.
        received = None

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[ParsedAttachment] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = _decode(part.get_filename())
        if disp == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                ParsedAttachment(
                    filename=filename or "attachment",
                    content_type=ctype,
                    content=payload,
                )
            )
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            text_parts.append(text[:_MAX_BODY])
        elif ctype == "text/html":
            html_parts.append(text[:_MAX_BODY])

    body_text = _join_capped(text_parts)
    raw_html = _join_capped(html_parts)
    body_html = email_svc._sanitize_html(raw_html) if raw_html else None

    return ParsedMessage(
        sender_email=(addr or "").lower(),
        sender_name=_decode(name) or None,
        to_addr=_decode(msg.get("To")) or None,
        subject=subject[:512],
        # str() before .strip(): with a raw 8-bit byte in the header, the email
        # package hands back a Header instance rather than a str, and Header has
        # no .strip() - AttributeError, which aborts the poll before the UID
        # highwater advances and wedges all inbound ingestion (audit
        # 2026-07-30). Same reason _decode() swallows everything.
        message_id=str(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=str(msg.get("In-Reply-To") or "").strip() or None,
        received_at=received,
        classification=inbound_classify.classify(msg),
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
