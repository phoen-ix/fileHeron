"""Inbound mailbox service tests (v1.27.0) - classify, parse, ingest dedup,
poll gating + fake IMAP, attachment AV gating."""
from __future__ import annotations

import contextlib

import pytest

from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass
from app.models.user import UserRole
from app.services import av_scan, imap_poll, inbound_mail, inbound_parse
from app.services import settings as s

NORMAL = b"""From: Grace Hopper <grace@example.com>
To: noreply@fileheron.local
Subject: Re: Your files
Message-ID: <n1@example.com>
Date: Mon, 01 Jun 2026 10:00:00 +0000
Content-Type: text/plain

Thanks, got them!
"""

BOUNCE = b"""From: MAILER-DAEMON@mx.example.com
To: noreply@fileheron.local
Subject: Mail delivery failed
Message-ID: <b1@mx>
Content-Type: multipart/report; report-type=delivery-status; boundary=xx

--xx
Content-Type: text/plain

client@dead.example does not exist.
--xx
Content-Type: message/delivery-status

Final-Recipient: rfc822; client@dead.example
Action: failed
--xx--
"""

AUTO = b"""From: Jane <jane@example.com>
To: noreply@fileheron.local
Subject: Out of office
Message-ID: <a1@example.com>
Auto-Submitted: auto-replied
Content-Type: text/plain

I am away until Monday.
"""

WITH_ATTACH = b"""From: Bob <bob@example.com>
To: noreply@fileheron.local
Subject: doc
Message-ID: <att1@example.com>
Content-Type: multipart/mixed; boundary=bb

--bb
Content-Type: text/plain

see attached
--bb
Content-Type: application/pdf
Content-Disposition: attachment; filename="report.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--bb--
"""


class _FakeSession:
    def __init__(self, msgs: dict[int, bytes], uidvalidity: int = 100):
        self.msgs = msgs
        self.uidvalidity = uidvalidity
        self.message_count = len(msgs)
        self.seen: list[int] = []
        self.moved: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    def select(self, mailbox):
        return self.uidvalidity

    def search_uids_after(self, last):
        return sorted(u for u in self.msgs if u > last)

    def fetch_raw(self, uid):
        return self.msgs.get(uid)

    def mark_seen(self, uid):
        self.seen.append(uid)

    def move(self, uid, folder):
        self.moved.append((uid, folder))

    def delete(self, uid):
        self.deleted.append(uid)

    def list_folders(self):
        return ["INBOX", "fileHeron/Processed"]


def _opener(session):
    @contextlib.contextmanager
    def _cm(_cfg):
        yield session
    return _cm


def _enable(db):
    s.set_value(db, key=s.Keys.IMAP_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.example.com", actor=None)
    db.commit()


# --- classify ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    (NORMAL, MessageClass.normal),
    (BOUNCE, MessageClass.bounce),
    (AUTO, MessageClass.auto_reply),
])
def test_classify(raw, expect):
    assert inbound_parse.parse(raw).classification == expect


def test_parse_fields_and_attachment():
    p = inbound_parse.parse(WITH_ATTACH)
    assert p.sender_email == "bob@example.com"
    assert p.subject == "doc"
    assert len(p.attachments) == 1
    assert p.attachments[0].filename == "report.pdf"


# --- ingest -----------------------------------------------------------------

def test_ingest_dedup(db):
    """Dedup is keyed on (uidvalidity, imap_uid) - server-assigned, stable, and
    what actually makes re-polling idempotent."""
    p = inbound_parse.parse(NORMAL)
    assert inbound_mail.ingest(db, p, uid=5, uidvalidity=100) is not None
    db.commit()
    # same slot -> dedup
    assert inbound_mail.ingest(db, p, uid=5, uidvalidity=100) is None
    assert db.query(InboundMessage).count() == 1


def test_a_reused_message_id_does_not_delete_the_second_mail(db):
    """This test used to assert the opposite - "same message_id, different uid
    -> still dedup" - and that assertion is what kept the defect alive.

    Message-ID comes straight off the wire and is trivially forgeable, so
    letting it decide meant a sender who knew or guessed an already-ingested
    value could make a later genuine mail be treated as a duplicate. The poll
    then advances its UID highwater past it, so it is never reconsidered:
    silent, targeted mail loss. Non-adversarially, bulk senders, mailing lists
    and forwarding loops reuse Message-IDs by accident and lost mail the same
    way (audit #2, N-15).

    A duplicate row is recoverable. A missing one is not."""
    p = inbound_parse.parse(NORMAL)
    assert inbound_mail.ingest(db, p, uid=5, uidvalidity=100) is not None
    db.commit()

    second = inbound_mail.ingest(db, p, uid=6, uidvalidity=100)
    assert second is not None, (
        "a second mail reusing an ingested Message-ID was dropped; a forgeable "
        "header must not be able to delete mail"
    )
    assert db.query(InboundMessage).count() == 2


def test_the_reuse_is_still_recorded(db, caplog):
    """Not acted on, but not silent either - an operator can still see it."""
    import logging

    p = inbound_parse.parse(NORMAL)
    inbound_mail.ingest(db, p, uid=5, uidvalidity=100)
    db.commit()
    with caplog.at_level(logging.WARNING):
        inbound_mail.ingest(db, p, uid=6, uidvalidity=100)
    assert "reuses Message-ID" in caplog.text


def test_a_new_uidvalidity_is_a_different_mailbox(db):
    """UIDs are only unique within a UIDVALIDITY generation."""
    p = inbound_parse.parse(NORMAL)
    assert inbound_mail.ingest(db, p, uid=5, uidvalidity=100) is not None
    db.commit()
    assert inbound_mail.ingest(db, p, uid=5, uidvalidity=101) is not None


def test_ingest_matches_sender_user(make_user, db):
    make_user(email="grace@example.com", role=UserRole.client, password="Pass12345678!")
    msg = inbound_mail.ingest(db, inbound_parse.parse(NORMAL), uid=1, uidvalidity=1)
    db.commit()
    assert msg.sender_user_id is not None


def test_ingest_attachment_scanned_clean(db, monkeypatch):
    monkeypatch.setattr(
        av_scan, "scan_stream",
        lambda fh: av_scan.ScanResult(state="clean", signature=None, raw="ok"),
    )
    msg = inbound_mail.ingest(db, inbound_parse.parse(WITH_ATTACH), uid=1, uidvalidity=1)
    db.commit()
    att = db.query(InboundAttachment).filter_by(message_id=msg.id).one()
    assert att.av_state == AttachmentAVState.clean
    assert att.filename == "report.pdf"


def test_ingest_attachment_when_clamd_unavailable_is_pending_not_fatal(db, monkeypatch):
    """M10: a clamd outage during an attachment scan must NOT abort ingestion -
    the attachment is stored `pending` (gated) and the message still lands, so
    the IMAP highwater advances and the inbox doesn't silently stall."""
    def _boom(_fh):
        raise av_scan.AVUnavailableError("clamd down")

    monkeypatch.setattr(av_scan, "scan_stream", _boom)
    msg = inbound_mail.ingest(db, inbound_parse.parse(WITH_ATTACH), uid=1, uidvalidity=1)
    db.commit()
    assert msg is not None
    att = db.query(InboundAttachment).filter_by(message_id=msg.id).one()
    assert att.av_state == AttachmentAVState.pending
    assert att.filename == "report.pdf"


# --- poll gating ------------------------------------------------------------

def test_poll_skips_when_disabled(db):
    assert imap_poll.run_poll(manual=False, db=db, session_opener=_opener(_FakeSession({})))["skipped"] == "disabled"


# NOTE (v1.28.0): imap_poll no longer self-gates on check_mode/interval -
# cadence/enable is owned by the cron scheduler. Only the imap.enabled feature
# guard remains (test_poll_skips_when_disabled covers it).


def test_poll_ingests_and_marks_seen(db, monkeypatch):
    monkeypatch.setattr(
        av_scan, "scan_stream",
        lambda fh: av_scan.ScanResult(state="clean", signature=None, raw="ok"),
    )
    _enable(db)
    s.set_value(db, key=s.Keys.IMAP_POST_FETCH_ACTION, value="mark_read", actor=None)
    db.commit()
    sess = _FakeSession({5: NORMAL, 6: BOUNCE})
    r = imap_poll.run_poll(manual=True, db=db, session_opener=_opener(sess))
    assert r["ingested"] == 2
    assert r["total"] == 2 and r["mailbox"] == "INBOX"
    assert set(sess.seen) == {5, 6}
    assert db.query(InboundMessage).count() == 2
    # re-poll: highwater means nothing new
    sess2 = _FakeSession({5: NORMAL, 6: BOUNCE})
    r2 = imap_poll.run_poll(manual=True, db=db, session_opener=_opener(sess2))
    assert r2["ingested"] == 0


def test_reuses_smtp_credentials_by_default(db):
    from app.services import imap_config
    s.set_value(db, key=s.Keys.SMTP_HOST, value="mail.example.com", actor=None)
    s.set_value(db, key=s.Keys.SMTP_USER, value="bot@example.com", actor=None)
    s.set_value(db, key=s.Keys.SMTP_PASSWORD, value="smtp-pass", actor=None)
    db.commit()
    cfg = imap_config.resolve_imap_config(db)
    assert cfg.user == "bot@example.com"
    assert cfg.password == "smtp-pass"
    assert cfg.host == "mail.example.com"  # falls back when no IMAP host set
    # A distinct IMAP host overrides, but creds still come from SMTP.
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.example.com", actor=None)
    db.commit()
    cfg = imap_config.resolve_imap_config(db)
    assert cfg.host == "imap.example.com" and cfg.user == "bot@example.com"


def test_toggle_off_uses_imap_specific_credentials(db):
    from app.services import imap_config
    s.set_value(db, key=s.Keys.SMTP_USER, value="bot@example.com", actor=None)
    s.set_value(db, key=s.Keys.SMTP_PASSWORD, value="smtp-pass", actor=None)
    s.set_value(db, key=s.Keys.IMAP_USE_SMTP_CREDENTIALS, value="false", actor=None)
    s.set_value(db, key=s.Keys.IMAP_USER, value="imapuser", actor=None)
    s.set_value(db, key=s.Keys.IMAP_PASSWORD, value="imap-pass", actor=None)
    db.commit()
    cfg = imap_config.resolve_imap_config(db)
    assert cfg.user == "imapuser" and cfg.password == "imap-pass"


def test_poll_move_action(db):
    _enable(db)
    s.set_value(db, key=s.Keys.IMAP_POST_FETCH_ACTION, value="move", actor=None)
    s.set_value(db, key=s.Keys.IMAP_MOVE_FOLDER, value="Done", actor=None)
    db.commit()
    sess = _FakeSession({7: NORMAL})
    imap_poll.run_poll(manual=True, db=db, session_opener=_opener(sess))
    assert sess.moved == [(7, "Done")]
