"""Regression: Message.get_param returns a (charset, lang, value) tuple for an
RFC2231-encoded parameter; classify must not AttributeError on .lower()."""
from __future__ import annotations

from email import message_from_string

from app.services.inbound_classify import classify


def test_classify_survives_tuple_report_type(monkeypatch):
    raw = "From: mailer-daemon@example.com\nContent-Type: multipart/report\nSubject: t\n\nbody\n"
    msg = message_from_string(raw)
    # Simulate an RFC2231-encoded `report-type*=...` param -> get_param returns a tuple.
    monkeypatch.setattr(msg, "get_param", lambda *a, **k: ("utf-8", "", "delivery-status"))
    classify(msg)  # must not raise (pre-fix: tuple.lower() -> AttributeError)
