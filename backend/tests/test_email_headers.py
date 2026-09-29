"""The headers every outgoing mail carries, asserted on the message
`build_message` produces and on its serialised bytes - every sender (the worker,
the auth flows, the SMTP test, the ops alert script) goes through it.

An analysis of a real alert mail found three defects that hit every mail:
- From read `file:Heron <x@y>;` - the product's own name has a colon, and an
  unquoted colon in a display name is RFC 5322 GROUP syntax (a group "file"),
  which Gmail rejects and DMARC cannot take a From domain from;
- no Date and no Message-ID, both mandatory (RFC 5322 s3.6).
Asserting on the re-parsed bytes is what catches the first: the object's own
header view looks plausible either way.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

import pytest

from app.utils.emailing import SmtpConfig, build_message


def _cfg(name: str = "file:Heron", addr: str = "fileheron@monumental.at") -> SmtpConfig:
    return SmtpConfig(host="smtp.example.com", port=587, user="", password="",
                      from_email=addr, from_name=name)


def _sent(cfg: SmtpConfig | None = None, **kw):
    """Build, serialise, and parse back - what the receiving server sees."""
    msg = build_message(cfg or _cfg(), to="fh@monumental.at", subject="An alert",
                        text_body="text", html_body=kw.pop("html_body", "<p>html</p>"), **kw)
    raw = msg.as_bytes()
    return raw, message_from_bytes(raw, policy=policy.default)


def test_from_is_one_quoted_mailbox_not_a_group():
    raw, back = _sent()
    assert b'From: "file:Heron" <fileheron@monumental.at>\n' in raw
    assert b"fileheron@monumental.at>;" not in raw
    header = back["From"]
    assert header.defects == ()
    assert [(a.display_name, a.addr_spec) for a in header.addresses] == [
        ("file:Heron", "fileheron@monumental.at")
    ]
    # A plain mailbox is one unnamed group; a named one is what the bug produced.
    assert [g.display_name for g in header.groups] == [None]


@pytest.mark.parametrize("name", ["Müller & Söhne", "Files, Inc.", 'Say "hi"', ""])
def test_any_display_name_round_trips(name):
    _raw, back = _sent(_cfg(name=name))
    [addr] = back["From"].addresses
    assert (addr.display_name, addr.addr_spec) == (name, "fileheron@monumental.at")
    assert back["From"].defects == ()


def test_date_is_present_and_utc():
    raw, back = _sent()
    when = parsedate_to_datetime(back["Date"])
    assert when.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - when) < timedelta(minutes=1)
    assert b"\nDate: " in raw


def test_message_id_uses_the_sender_domain_and_is_unique():
    _raw, first = _sent()
    _raw, second = _sent()
    assert first["Message-ID"].endswith("@monumental.at>")
    assert first["Message-ID"].startswith("<")
    assert first["Message-ID"] != second["Message-ID"]


def test_a_sender_without_a_domain_still_gets_a_message_id():
    _raw, back = _sent(_cfg(addr="localpart-only"))
    assert back["Message-ID"].startswith("<") and back["Message-ID"].endswith(">")


def test_every_mail_says_it_is_automatic():
    _raw, back = _sent()
    assert back["Auto-Submitted"] == "auto-generated"
    assert back["X-Auto-Response-Suppress"] == "All"


def test_only_the_top_level_carries_mime_version():
    _raw, back = _sent()
    assert back["MIME-Version"] == "1.0"
    parts = list(back.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert all(p["MIME-Version"] is None for p in parts)


def test_a_text_only_mail_has_the_same_headers():
    msg = build_message(_cfg(), to="fh@monumental.at", subject="s", text_body="t")
    for header in ("From", "Date", "Message-ID", "Auto-Submitted", "X-Auto-Response-Suppress"):
        assert msg[header], header


def test_unsubscribe_headers_still_appear_only_when_passed():
    _raw, plain = _sent()
    assert plain["List-Unsubscribe"] is None and plain["List-Unsubscribe-Post"] is None
    _raw, listed = _sent(list_unsubscribe="<https://fh.example/api/notification-subscriptions/t/one-click>")
    assert listed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
