"""The inbound path, hardened - audit #2.

`inbound` is one of the two dimensions that crashed during the 2026-07-30 sweep
and never re-ran, so this whole family had never been examined. Each test below
is one finding; each names what it would cost.
"""
from __future__ import annotations

import pytest

from app.models.inbound_attachment import InboundAttachment
from app.models.inbound_message import InboundMessage
from app.models.user import UserRole
from app.services import imap_client, imap_poll, inbound_mail, inbound_parse
from app.services import settings as s

SENDER = "grace@example.com"


def _raw(sender: str = SENDER, *, body: bytes = b"hello") -> bytes:
    return (
        f"From: Grace <{sender}>\r\n"
        "To: inbox@fileheron.local\r\n"
        "Subject: hi\r\n"
        "Message-ID: <m1@example.com>\r\n"
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + body


# --- the mailbox name goes on the wire quoted -------------------------------


def test_a_mailbox_name_with_a_space_is_quoted():
    """`[Gmail]/All Mail`, `Sent Items`, `Archive/Bearbeitete Mails` - imaplib
    concatenates arguments with a bare space, so the server saw two tokens and
    answered NO: `select` raised on every tick and the poll never ran."""
    assert imap_client._mbox("[Gmail]/All Mail") == '"[Gmail]/All Mail"'
    assert imap_client._mbox('weird"name') == '"weird\\"name"'


@pytest.mark.parametrize("evil", ["INBOX\r\nX1 DELETE Archive", "INBOX\nLOGOUT", "IN\x00BOX"])
def test_a_newline_in_a_mailbox_name_is_refused(evil):
    """CR/LF terminates an IMAP command, so the tail is executed as a further
    command against a third-party mail account - reachable by an admin, or by
    anyone who can restore a crafted config backup (it reinstates app_settings
    wholesale)."""
    with pytest.raises(ValueError):
        imap_client._mbox(evil)


# --- UIDVALIDITY ------------------------------------------------------------


class _Conn:
    """Faithful imaplib stand-in.

    The load-bearing detail: after SELECT, imaplib files `* OK [UIDVALIDITY 42]`
    under the "OK" key of `untagged_responses`, NOT under "UIDVALIDITY" - so
    `response("UIDVALIDITY")` answers (OK, [None]) even though the server sent
    it. Reproducing that is what makes this test able to fail; a stub that
    answers "UIDVALIDITY" directly passes against the broken code too.
    """

    def __init__(self, *, status_ok: bool = False):
        self.status_ok = status_ok
        self.commands: list[str] = []

    def select(self, mailbox):
        self.commands.append(f"SELECT {mailbox}")
        return "OK", [b"17"]

    def response(self, key):
        self.commands.append(f"RESPONSE {key}")
        if key == "OK":
            return "OK", [b"[UIDVALIDITY 42] UIDs valid"]
        return "OK", [None]

    def status(self, mailbox, what):
        self.commands.append(f"STATUS {mailbox}")
        if not self.status_ok:
            return "NO", [b"STATUS not allowed on selected mailbox"]
        return "OK", [b'"INBOX" (UIDVALIDITY 42)']


def test_uidvalidity_comes_from_the_select_response():
    """Read from SELECT, a server that refuses STATUS no longer answers 0.

    0 meant `imap.uidvalidity` was never stored, so a mailbox migration - the
    exact event UIDVALIDITY exists to signal - was invisible: `last_uid` stayed
    at the old incarnation's highwater, `UID SEARCH n:*` matched nothing, and
    every poll reported fetched=0 ok=True while 100% of inbound mail was
    ignored, indefinitely, behind a green Scheduled Tasks page."""
    sess = imap_client.ImapSession(_Conn(status_ok=False))
    assert sess.select("INBOX") == 42
    assert sess.message_count == 17


def test_the_status_fallback_still_works():
    class _NoUidValidity(_Conn):
        def response(self, key):
            return "OK", [None]  # a server that sends no UIDVALIDITY code

    sess = imap_client.ImapSession(_NoUidValidity(status_ok=True))
    assert sess.select("INBOX") == 42


# --- destructive server actions ---------------------------------------------


class _MoveConn:
    """Answers NO to CREATE, MOVE and COPY - a restricted or quota-full
    mailbox."""

    def __init__(self):
        self.deleted: list[str] = []

    def create(self, folder):
        return "NO", [b"cannot create"]

    def uid(self, cmd, *args):
        if cmd in ("MOVE", "COPY"):
            return "NO", [b"[TRYCREATE]"]
        if cmd == "STORE":
            self.deleted.append(args[0])
            return "OK", [b""]
        if cmd == "EXPUNGE":
            return "OK", [b""]
        return "OK", [b""]

    def expunge(self):
        self.deleted.append("EXPUNGE-ALL")
        return "OK", [b""]


def test_a_failed_move_does_not_delete_the_message():
    """MOVE NO -> COPY NO -> delete() ran anyway, permanently expunging the
    client's original mail - headers, DKIM signature and any attachment
    fileHeron had failed to store - from the server."""
    conn = _MoveConn()
    sess = imap_client.ImapSession(conn)
    with pytest.raises(RuntimeError):
        sess.move(7, "Archive/Processed")
    assert conn.deleted == [], "the only copy of the message was destroyed"


def test_delete_expunges_only_its_own_message():
    """A bare EXPUNGE destroys every OTHER message a human already flagged
    \\Deleted in their own mail client - the standard "delete now, expunge
    later" idiom - silently, with no audit row."""
    calls: list[tuple] = []

    class _C:
        def uid(self, cmd, *args):
            calls.append((cmd, args))
            return "OK", [b""]

        def expunge(self):
            calls.append(("EXPUNGE-ALL", ()))
            return "OK", [b""]

    imap_client.ImapSession(_C()).delete(7)
    assert ("EXPUNGE-ALL", ()) not in calls
    assert any(c[0] == "EXPUNGE" for c in calls)


# --- the unknown-sender gate ------------------------------------------------


def test_mail_from_an_unknown_sender_is_refused_before_anything_is_stored(db, monkeypatch):
    """"No anonymous senders by policy" was documented and not implemented: any
    internet sender could land admin-downloadable attachments on the storage
    backend, attributable to no user, counted against no quota and behind no
    rate limit. 50,000 x 40 MB fills the volume MariaDB and every upload
    share."""
    parsed = inbound_parse.parse(_raw("stranger@nowhere.example"))
    with pytest.raises(inbound_mail.UnknownSenderError):
        inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1)
    assert db.query(InboundMessage).count() == 0


def test_a_registered_sender_is_ingested(db, make_user):
    make_user(email=SENDER, role=UserRole.client)
    db.commit()
    parsed = inbound_parse.parse(_raw())
    msg = inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1)
    assert msg is not None
    assert msg.sender_user_id is not None


def test_a_disabled_users_address_does_not_count_as_known(db, make_user):
    u = make_user(email=SENDER, role=UserRole.client)
    u.is_disabled = True
    db.commit()
    parsed = inbound_parse.parse(_raw())
    with pytest.raises(inbound_mail.UnknownSenderError):
        inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1)


def test_the_gate_can_be_turned_off(db):
    s.set_value(db, key=s.Keys.IMAP_REQUIRE_KNOWN_SENDER, value="false", actor=None)
    db.commit()
    parsed = inbound_parse.parse(_raw("stranger@nowhere.example"))
    assert inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1) is not None


# --- attachments ------------------------------------------------------------


def _mk_attachment_mail(n: int, *, size: int = 4) -> bytes:
    parts = b"".join(
        b'--bb\r\nContent-Type: application/octet-stream\r\n'
        b'Content-Disposition: attachment; filename="a%d.bin"\r\n\r\n%s\r\n'
        % (i, b"x" * size)
        for i in range(n)
    )
    return (
        f"From: Grace <{SENDER}>\r\n"
        "Subject: many\r\n"
        "Message-ID: <many@example.com>\r\n"
        "Content-Type: multipart/mixed; boundary=bb\r\n\r\n"
    ).encode() + parts + b"--bb--\r\n"


def test_the_attachment_count_is_bounded(db, make_user, monkeypatch, tmp_path):
    """A 16 MB mail can declare ~171,000 minimal parts. One blob, one clamd
    session and one row each ran for hours and exhausted the volume's inodes -
    and every guard before this one passes, because each part is a byte."""
    from app.services import av_scan

    make_user(email=SENDER, role=UserRole.client)
    db.commit()
    monkeypatch.setattr(
        av_scan, "scan_stream", lambda _fh: type("R", (), {"state": "clean"})()
    )
    parsed = inbound_parse.parse(_mk_attachment_mail(inbound_mail.MAX_ATTACHMENTS_PER_MESSAGE + 20))
    msg = inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1)
    db.flush()
    stored = db.query(InboundAttachment).filter(
        InboundAttachment.message_id == msg.id
    ).count()
    assert stored == inbound_mail.MAX_ATTACHMENTS_PER_MESSAGE
    assert "not stored" in (msg.body_text or ""), (
        "the admin has no way to learn that attachments were discarded"
    )


def test_a_storage_failure_is_not_silently_a_paperclip(db, make_user, monkeypatch):
    """The bytes failed to store, the row was never created, and the message was
    committed with has_attachments=True and zero attachments - then
    post_fetch_action=delete expunged the mail, so the client's file existed
    nowhere at all."""
    from app.services import av_scan
    from app.services import storage_backend as storage_svc

    make_user(email=SENDER, role=UserRole.client)
    db.commit()
    monkeypatch.setattr(
        av_scan, "scan_stream", lambda _fh: type("R", (), {"state": "clean"})()
    )

    backend = storage_svc.get_storage_backend()
    monkeypatch.setattr(
        backend, "finalize", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
    )
    monkeypatch.setattr(storage_svc, "get_storage_backend", lambda: backend)

    parsed = inbound_parse.parse(_mk_attachment_mail(1))
    msg = inbound_mail.ingest(db, parsed, uid=1, uidvalidity=1)
    assert msg.has_attachments is False, "a paperclip for an attachment that is not there"
    assert getattr(msg, "_fh_incomplete", False) is True, (
        "the poll needs to know not to apply a destructive server action"
    )
    assert "could not be stored" in (msg.body_text or "")


def test_the_total_body_size_is_capped(db, make_user):
    """Per-part was not a bound. Twenty 1 MB text parts assembled a 20 MB
    body_text, whose INSERT exceeds MariaDB's 16 MB max_allowed_packet: the
    server drops the connection, the handler swallows it and advances the
    highwater, and the mail is gone from fileHeron while still sitting unread on
    the server."""
    make_user(email=SENDER, role=UserRole.client)
    db.commit()
    parts = b"".join(
        b"--bb\r\nContent-Type: text/plain\r\n\r\n" + (b"z" * 900_000) + b"\r\n"
        for _ in range(20)
    )
    raw = (
        f"From: Grace <{SENDER}>\r\n"
        "Subject: long\r\n"
        "Content-Type: multipart/mixed; boundary=bb\r\n\r\n"
    ).encode() + parts + b"--bb--\r\n"
    parsed = inbound_parse.parse(raw)
    assert len(parsed.body_text) <= inbound_parse._MAX_BODY_TOTAL + 200
    assert "truncated" in parsed.body_text


# --- run-level bounds -------------------------------------------------------


def test_the_poll_lock_outlives_the_job_it_protects():
    """The lock (900 s) expired under a run the ARQ timeout allows to last
    2100 s, so the next tick started a second poll against the same mailbox:
    with post_fetch_action=delete the newcomer expunged a message the first run
    was still mid-handling, and the two raced on the highwater."""
    from app.workers import worker

    assert worker.WorkerSettings.job_timeout < imap_poll._POLL_LOCK_TTL_SEC


def test_a_run_handles_a_bounded_number_of_messages():
    """Enabling inbound against an account with years of history meant one run
    fetching tens of thousands of messages: "Fetch now" never returned, the lock
    expired mid-run, ARQ killed and retried while the original thread kept
    going, and with post_fetch_action=delete the run expunged the whole
    historical mailbox."""
    assert 0 < imap_poll.MAX_MESSAGES_PER_RUN <= 1000


def test_an_unknown_message_size_is_fetched_with_a_bound(db, make_user, monkeypatch):
    """`fetch_size` returns None whenever the server declines RFC822.SIZE, and
    the guard then fell through to an UNBOUNDED download - the one thing it
    exists to prevent. A 2 GB message OOM-kills a 512 MB worker, SIGKILL raises
    nothing, and the un-advanced highwater makes it permanent."""
    seen: dict = {}

    class _Sess:
        message_count = 1

        def select(self, _m):
            return 1

        def search_uids_after(self, _l):
            return [5]

        def fetch_size(self, _u):
            return None

        def fetch_raw(self, _u, *, max_bytes=None):
            seen["max_bytes"] = max_bytes
            return _raw()

        def mark_seen(self, _u):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    make_user(email=SENDER, role=UserRole.client)
    s.set_value(db, key=s.Keys.IMAP_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.example.invalid", actor=None)
    s.set_value(db, key=s.Keys.IMAP_USER, value="fh@example.invalid", actor=None)
    s.set_value(db, key=s.Keys.IMAP_PASSWORD, value="pw", actor=None)
    s.set_value(db, key=s.Keys.IMAP_USE_SMTP_CREDENTIALS, value="false", actor=None)
    db.commit()

    imap_poll.run_poll(manual=True, db=db, session_opener=lambda _cfg: _Sess())
    assert seen.get("max_bytes") == imap_poll.MAX_MESSAGE_BYTES, (
        "the message was downloaded with no ceiling at all"
    )
