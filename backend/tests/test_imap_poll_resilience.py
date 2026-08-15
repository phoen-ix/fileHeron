"""One malformed message must not stop inbound mail forever.

Three defects from the 2026-07-30 audit, all with the same blast radius - the
poll aborts before `last_uid` is persisted, so the next run starts from the same
highwater, hits the same message and dies identically. Inbound ingestion stops
permanently on a single mail, which is exactly the failure mode CLAUDE.md warns
about for this subsystem.

  1. A crafted Date raises OverflowError, which escaped inbound_parse's
     (TypeError, ValueError) guard.
  2. A raw 8-bit byte in a header makes email return a Header object rather than
     a str, so `.strip()` on Message-ID raised AttributeError.
  3. No per-message error boundary in run_poll at all, so ANY parse/ingest
     failure took the whole poll down.

Plus: run_poll catches everything and returns {"ok": False}, but @track_cron
only records a failure when the job RAISES - so a broken poll was logged as a
clean success every five minutes.
"""
from __future__ import annotations

import contextlib

import pytest

from app.services import imap_poll as imap_poll_svc
from app.services import inbound_parse

POISON_DATE = b"""From: sender@example.com
To: inbox@example.com
Subject: poison
Date: Mon, 1 Jan 999999999999 00:00:00 +0000
Message-ID: <poison-date@example.com>

body
"""

# Raw 8-bit byte in the FROM header. The existing POISON_8BIT below keeps From
# pure ASCII, which is exactly why this went unnoticed: under compat32 a From
# with a raw 8-bit byte comes back as an email.header.Header, not a str, so
# parseaddr returned ('','') and inbound_classify crashed on .lower(). An empty
# sender means require_known_sender (on by default) refuses the message at the
# pre-fetch gate, the UID highwater advances and commits, and the mail is never
# selected again - permanently invisible, recoverable only by hand on the server.
POISON_8BIT_FROM = b"""From: Jos\xe9 Garc\xeda <jose@client.example>
To: inbox@example.com
Subject: quarterly report
Message-ID: <8bit-from@client.example>

body
"""

# Raw 8-bit byte in Subject and Message-ID (never valid, trivially sent).
POISON_8BIT = b"""From: sender@example.com
To: inbox@example.com
Subject: caf\xe9 \xff\xfe
Message-ID: <poison-\xe9-8bit@example.com>

body
"""

GOOD = b"""From: sender@example.com
To: inbox@example.com
Subject: perfectly fine
Date: Mon, 1 Jan 2026 00:00:00 +0000
Message-ID: <good@example.com>

body
"""


# POISON_8BIT_FROM is deliberately NOT in this parametrisation: it carries a
# different sender, and loosening the assertion below to accommodate it would
# weaken a check that is doing real work for the other two.
@pytest.mark.parametrize("raw", [POISON_DATE, POISON_8BIT], ids=["date", "8bit"])
def test_parse_survives_hostile_headers(raw):
    """parse() itself must not raise - that is what aborts the poll."""
    parsed = inbound_parse.parse(raw)
    assert parsed.sender_email == "sender@example.com"


class _FakeSession:
    """Minimal IMAP session double driving run_poll's loop."""

    def __init__(self, messages: dict[int, bytes]):
        self._messages = messages
        self.message_count = len(messages)
        self.actions: list[tuple[str, int]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def select(self, _mailbox):
        return 1  # uidvalidity

    def search_uids_after(self, last_uid):
        return sorted(u for u in self._messages if u > last_uid)

    def fetch_raw(self, uid, *, max_bytes=None):
        return self._messages[uid]

    def mark_seen(self, uid):
        self.actions.append(("mark_seen", uid))

    def move(self, uid, _folder):
        self.actions.append(("move", uid))

    def delete(self, uid):
        self.actions.append(("delete", uid))


@pytest.fixture
def imap_enabled(db, monkeypatch):
    monkeypatch.setattr(imap_poll_svc.imap_config, "is_enabled", lambda _db: True)

    class _Cfg:
        is_configured = True
        mailbox = "INBOX"

    monkeypatch.setattr(
        imap_poll_svc.imap_config, "resolve_imap_config", lambda _db: _Cfg()
    )
    monkeypatch.setattr(imap_poll_svc.imap_config, "post_fetch_action", lambda _db: "none")
    monkeypatch.setattr(imap_poll_svc.imap_config, "move_folder", lambda _db: "")


def test_one_bad_message_does_not_stop_the_poll(db, imap_enabled, monkeypatch):
    """The wedge itself: a poison message must be skipped, the good message
    after it must still be ingested, and the highwater must advance past both -
    otherwise the next poll repeats the same death."""

    real_parse = inbound_parse.parse  # bind before patching, or _explode recurses

    def _explode(raw):
        if b"poison" in raw:
            raise ValueError("simulated parse failure")
        return real_parse(raw)

    monkeypatch.setattr(imap_poll_svc.inbound_parse, "parse", _explode)
    monkeypatch.setattr(
        imap_poll_svc.inbound_mail, "ingest", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        imap_poll_svc.inbound_mail, "ingested_by_uid", lambda *a, **k: False
    )

    session = _FakeSession({1: POISON_DATE, 2: GOOD})
    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _cfg: session
    )

    assert result["ok"] is True
    assert result["skipped"] == 1
    assert result["ingested"] == 1
    # Highwater past the poison message - this is what breaks the loop.
    assert result["last_uid"] == 2


def test_highwater_advances_past_an_uningestable_message(db, imap_enabled, monkeypatch):
    """Even if EVERY message fails, the poll must still move forward."""
    monkeypatch.setattr(
        imap_poll_svc.inbound_parse,
        "parse",
        lambda _raw: (_ for _ in ()).throw(ValueError("always fails")),
    )

    session = _FakeSession({7: POISON_DATE, 8: POISON_8BIT})
    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _cfg: session
    )

    assert result["ok"] is True
    assert result["skipped"] == 2
    assert result["last_uid"] == 8


@pytest.mark.asyncio
async def test_failed_poll_is_recorded_as_a_cron_failure(monkeypatch):
    """A broken mailbox must not be logged as a clean success."""
    from app.workers import imap_poll as worker

    monkeypatch.setattr(
        worker.imap_poll_svc,
        "run_poll",
        lambda **_kw: {"ok": False, "error": "ConnectionRefusedError: nope"},
    )
    with pytest.raises(worker.InboundPollError):
        await worker.imap_poll.__wrapped__(None)


@pytest.mark.asyncio
async def test_not_configured_is_not_an_error(monkeypatch):
    """Control: a deployment without inbound mail is not a fault, and alerting
    on it would train operators to ignore this job."""
    from app.workers import imap_poll as worker

    monkeypatch.setattr(
        worker.imap_poll_svc, "run_poll", lambda **_kw: {"ok": False, "error": "not_configured"}
    )
    with contextlib.suppress(AttributeError):
        result = await worker.imap_poll.__wrapped__(None)
        assert result["error"] == "not_configured"


def test_parse_survives_an_8bit_from():
    """Same contract as the parametrised cases above, with this fixture's own
    expected sender."""
    assert inbound_parse.parse(POISON_8BIT_FROM).sender_email == "jose@client.example"


def test_an_8bit_from_still_yields_a_sender_address():
    """The address is what decides whether the message is ingested at all."""
    parsed = inbound_parse.parse(POISON_8BIT_FROM)
    assert parsed.sender_email == "jose@client.example"
    assert "Jos" in parsed.sender_name


def test_classify_survives_an_8bit_from():
    """classify() reads From/Auto-Submitted/Precedence with str methods. On a
    Header object that is an AttributeError, which takes the whole poll down -
    a harder failure than the empty sender it was masking."""
    from email import message_from_bytes

    from app.services import inbound_classify

    assert inbound_classify.classify(message_from_bytes(POISON_8BIT_FROM)) is not None
