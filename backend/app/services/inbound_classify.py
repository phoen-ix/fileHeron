"""Classify an inbound message as a genuine reply, a bounce (DSN), or an
auto-reply (vacation / out-of-office / auto-ack) — from headers only. Pure +
side-effect free so it's exhaustively testable on raw sample emails (v1.27.0).
"""
from __future__ import annotations

from email.message import Message

from ..models.inbound_message import MessageClass

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
    report_type = (msg.get_param("report-type") or "").lower() if msg.get("Content-Type") else ""
    from_addr = (msg.get("From") or "").lower()
    subject = (msg.get("Subject") or "").lower()

    # --- Bounce (delivery-status notification) ---
    if ctype == "multipart/report" and report_type == "delivery-status":
        return MessageClass.bounce
    if msg.get("X-Failed-Recipients"):
        return MessageClass.bounce
    # A null Return-Path (<>) is the classic bounce envelope.
    if (msg.get("Return-Path") or "").strip() in ("<>", ""):
        if any(h in from_addr for h in _BOUNCE_SENDER_HINTS):
            return MessageClass.bounce
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
