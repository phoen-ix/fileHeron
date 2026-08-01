"""Classify an inbound message as a genuine reply, a bounce (DSN), or an
auto-reply (vacation / out-of-office / auto-ack) - from headers only. Pure +
side-effect free so it's exhaustively testable on raw sample emails (v1.27.0).
"""
from __future__ import annotations

from email.header import decode_header, make_header
from email.message import Message
from urllib.parse import unquote

from ..models.inbound_message import MessageClass


def _decoded_subject(msg: Message) -> str:
    raw = msg.get("Subject") or ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


_AUTO_SUBJECT_HINTS = (
    "out of office",
    "auto-reply",
    "autoreply",
    "automatic reply",
    "abwesenheit",
    "automatische antwort",
)
_BOUNCE_SENDER_HINTS = ("mailer-daemon", "postmaster")


def classify(msg: Message) -> MessageClass:
    ctype = (msg.get_content_type() or "").lower()
    # get_param returns a (charset, lang, value) tuple for an RFC2231-encoded
    # parameter. Guarding the type stopped the AttributeError but DISCARDED the
    # value, so a legal `report-type*=us-ascii''delivery-status` - which real
    # MTAs emit - was classified `normal`: with notify_mode=human every admin
    # got a "new inbound message" mail for a bounce, and in the inbox the bounce
    # was indistinguishable from a client reply (audit #2). Decode it instead.
    _rt = msg.get_param("report-type") if msg.get("Content-Type") else None
    if isinstance(_rt, tuple):
        _rt = _rt[2] if len(_rt) == 3 else ""
        try:
            _rt = unquote(_rt)
        except Exception:
            _rt = ""
    report_type = (_rt if isinstance(_rt, str) else "").lower()
    from_addr = (msg.get("From") or "").lower()
    # Decode RFC2047-encoded words (=?utf-8?...?=) before matching, else an
    # encoded "Automatische Antwort" subject never matches the auto-reply hints.
    subject = _decoded_subject(msg).lower()

    # --- Bounce (delivery-status notification) ---
    if ctype == "multipart/report" and report_type == "delivery-status":
        return MessageClass.bounce
    if msg.get("X-Failed-Recipients"):
        return MessageClass.bounce
    # A null Return-Path (<>) is the classic bounce envelope; and a daemon
    # sender is a bounce regardless of Return-Path.
    if any(h in from_addr for h in _BOUNCE_SENDER_HINTS):
        return MessageClass.bounce

    # --- Auto-reply ---
    auto_submitted = (msg.get("Auto-Submitted") or "").lower()
    if auto_submitted and auto_submitted != "no":
        return MessageClass.auto_reply
    precedence = (msg.get("Precedence") or "").lower()
    if precedence in ("auto_reply", "bulk", "junk", "list"):
        return MessageClass.auto_reply
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return MessageClass.auto_reply
    if any(h in subject for h in _AUTO_SUBJECT_HINTS):
        return MessageClass.auto_reply

    return MessageClass.normal
