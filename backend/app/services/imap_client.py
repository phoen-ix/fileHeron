"""Thin IMAP session over stdlib ``imaplib`` (v1.27.0).

Sync - the poll runs it in a worker thread (``asyncio.to_thread``). All raw IMAP
I/O lives behind ``ImapSession`` so the poll logic can be driven by a fake in
tests. No third-party dependency (``aioimaplib`` is GPLv3; avoided).
"""
from __future__ import annotations

import imaplib
import logging
import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from .imap_config import ImapConfig

logger = logging.getLogger("fileheron.imap")

_TIMEOUT = 30


def _mbox(name: str) -> str:
    """Quote a mailbox name for the wire.

    `imaplib` concatenates command arguments with a space and no quoting, so a
    name with a space in it - `[Gmail]/All Mail`, `Sent Items`,
    `Archive/Bearbeitete Mails` - was parsed by the server as two tokens and
    answered NO or BAD: `select` raised on every tick, or the post-fetch move
    failed forever inside a swallowed except. A CR or LF is worse than a
    breakage: it terminates the command, so the rest of the value is executed
    as a further IMAP command against a third-party mail account (audit #2).
    """
    if "\r" in name or "\n" in name or "\x00" in name:
        raise ValueError("IMAP mailbox names cannot contain CR, LF or NUL")
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapSession:
    """One authenticated IMAP connection. Construct via ``open_session``."""

    def __init__(self, conn: imaplib.IMAP4):
        self._c = conn
        # EXISTS count of the last-selected mailbox (total messages in it).
        self.message_count = 0

    # --- mailbox selection -------------------------------------------------
    def select(self, mailbox: str) -> int:
        """Select ``mailbox`` (read-write) and return its UIDVALIDITY. Also
        records the mailbox's EXISTS (total message) count on ``message_count``."""
        typ, data = self._c.select(_mbox(mailbox))
        if typ != "OK":
            raise RuntimeError(f"IMAP SELECT {mailbox!r} failed: {typ}")
        try:
            self.message_count = int(data[0]) if data and data[0] else 0
        except (TypeError, ValueError):
            self.message_count = 0

        # SELECT's own untagged `* OK [UIDVALIDITY n]` is the authoritative
        # answer and always present (RFC 3501 6.3.1). This used to read
        # `response("UIDVALIDITY")`, which imaplib does not populate from the
        # response CODE - so the branch never fired and every mailbox fell
        # through to STATUS. On a server that refuses STATUS for the selected
        # mailbox the result was 0, forever: `imap.uidvalidity` was never
        # stored, so a mailbox migration - the exact event UIDVALIDITY exists
        # to signal - could not be detected, `last_uid` stayed at the old
        # incarnation's highwater, and every poll returned fetched=0 ok=True
        # while 100% of inbound mail was ignored (audit #2).
        uidvalidity = self._uidvalidity_from_untagged()
        if uidvalidity:
            return uidvalidity
        typ, data = self._c.status(_mbox(mailbox), "(UIDVALIDITY)")
        if typ == "OK" and data and data[0]:
            m = re.search(rb"UIDVALIDITY (\d+)", data[0])
            if m:
                return int(m.group(1))
        return 0

    def _uidvalidity_from_untagged(self) -> int:
        for key in ("UIDVALIDITY", "OK"):
            try:
                typ, data = self._c.response(key)
            except Exception:
                continue
            if typ != "OK" or not data:
                continue
            for item in data:
                raw = item if isinstance(item, bytes) else str(item).encode()
                if raw is None:
                    continue
                if raw.strip().isdigit():
                    return int(raw.strip())
                m = re.search(rb"UIDVALIDITY\s+(\d+)", raw)
                if m:
                    return int(m.group(1))
        return 0

    def list_folders(self) -> list[str]:
        typ, data = self._c.list()
        if typ != "OK" or not data:
            return []
        out: list[str] = []
        for raw in data:
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            # `(\HasNoChildren) "/" "INBOX"` → take the quoted tail.
            name = line.rsplit(' "', 1)[-1].strip('"') if '"' in line else line.split()[-1]
            out.append(name)
        return out

    # --- fetch -------------------------------------------------------------
    def search_uids_after(self, last_uid: int) -> list[int]:
        # First run (no highwater) -> "ALL" is the most portable; incremental
        # runs use a UID range. UID SEARCH returns UIDs either way.
        criterion = "ALL" if last_uid <= 0 else f"UID {last_uid + 1}:*"
        # `None` is the charset placeholder UID SEARCH takes; imaplib passes
        # it through, but its stub types every arg as str.
        typ, data = self._c.uid("SEARCH", cast("str", None), criterion)
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [int(x) for x in data[0].split()]
        # `<n>:*` returns the highest message when n exceeds it - filter.
        return sorted(u for u in uids if u > last_uid)

    def fetch_size(self, uid: int) -> int | None:
        """RFC822.SIZE for a message, without downloading it.

        The server reports this from its own index, so it costs nothing and
        lets the caller refuse an oversize message BEFORE it is materialised.
        Returns None when the server does not answer, in which case the caller
        should fall through to fetching (fail-open on a metadata read)."""
        try:
            typ, data = self._c.uid("FETCH", str(uid), "(RFC822.SIZE)")
        except Exception:
            return None
        if typ != "OK" or not data:
            return None
        for part in data:
            raw = part if isinstance(part, bytes) else (part[0] if isinstance(part, tuple) else b"")
            m = re.search(rb"RFC822\.SIZE\s+(\d+)", raw or b"")
            if m:
                return int(m.group(1))
        return None

    def fetch_headers(self, uid: int) -> bytes | None:
        """The message's headers only.

        Used to decide whether this instance wants the message at all before
        downloading it. The known-sender gate refuses mail from an address with
        no account, and refusing it AFTER pulling a 40 MB attachment across the
        wire spends exactly the resource the gate exists to protect (audit #2
        cross-check).
        """
        typ, data = self._c.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
        if typ != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                return part[1]
        return None

    def fetch_raw(self, uid: int, *, max_bytes: int | None = None) -> bytes | None:
        """Download a message. With `max_bytes`, ask for a partial body instead
        of the whole thing.

        The caller normally refuses an oversize message from `fetch_size`
        first - but that returns None whenever the server declines RFC822.SIZE
        (Exchange front-ends and some proxies do, for certain message states),
        and the old behaviour was to fall through to an UNBOUNDED download.
        A 2 GB message then OOM-killed a worker capped at 512 MB, which SIGKILL
        makes uncatchable and which the un-advanced highwater made permanent
        (audit #2). RFC 3501 6.4.5 partial fetch bounds it: ask for one byte
        more than the cap, and a body that comes back at the cap is oversize.
        """
        spec = "(RFC822)" if max_bytes is None else f"(BODY.PEEK[]<0.{max_bytes + 1}>)"
        typ, data = self._c.uid("FETCH", str(uid), spec)
        if typ != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                return part[1]
        return None

    # --- post-fetch actions ------------------------------------------------
    def mark_seen(self, uid: int) -> None:
        self._c.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")

    def delete(self, uid: int) -> None:
        self._c.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        # UID EXPUNGE (RFC 4315), not a bare EXPUNGE: a mailbox-wide expunge
        # also destroys every OTHER message a human already flagged \Deleted in
        # their own mail client - the standard "delete now, expunge later"
        # idiom - with no audit row and no notification (audit #2). Servers
        # without UIDPLUS answer BAD; only then fall back.
        try:
            typ, _ = self._c.uid("EXPUNGE", str(uid))
            if typ == "OK":
                return
        except imaplib.IMAP4.error:
            pass
        self._c.expunge()

    def move(self, uid: int, folder: str) -> None:
        quoted = _mbox(folder)
        try:
            self._c.create(quoted)  # no-op if it already exists
        except imaplib.IMAP4.error:
            pass
        try:
            typ, _ = self._c.uid("MOVE", str(uid), quoted)
        except imaplib.IMAP4.error:
            # A server without RFC 6851 MOVE answers a tagged BAD, which imaplib
            # raises rather than returning; fall through to COPY+delete.
            typ = "BAD"
        if typ == "OK":
            return
        try:
            typ, _ = self._c.uid("COPY", str(uid), quoted)
        except imaplib.IMAP4.error:
            typ = "BAD"
        if typ != "OK":
            # COPY failed too - the message is NOT in the target folder, so
            # deleting it here would destroy the only copy. This used to fall
            # straight through to `delete(uid)`, which on a mailbox where the
            # account cannot create the folder (restricted Exchange
            # permissions, quota-full account) expunged the client's original
            # mail, headers, DKIM signature and all (audit #2).
            raise RuntimeError(f"IMAP MOVE/COPY to {folder!r} failed: {typ}")
        self.delete(uid)


def _tls_context(cfg: ImapConfig) -> ssl.SSLContext:
    """The TLS context for an IMAP connection.

    `imaplib.IMAP4_SSL(...)` and `IMAP4.starttls()` with no `ssl_context` both
    fall back to `ssl._create_stdlib_context()`, which is an alias for
    `_create_unverified_context`: `verify_mode=CERT_NONE`, `check_hostname=False`,
    no CA store. So BOTH of the modes this product presents as the secure ones
    accepted any certificate for any hostname, and anyone on the path between
    the worker and the mail provider could complete the handshake and read the
    LOGIN that follows.

    What that leaks is not an isolated mailbox password:
    `imap_config.uses_smtp_credentials` defaults to True, so these are the SMTP
    credentials - the account this instance sends all outbound mail from.

    Every piece of prose around it said the opposite. `imap_config` logs an
    error only for `tls_mode='none'` "because that is almost certainly a
    mistake", framing implicit/starttls as safe; the poll's own error text tells
    admins to "use implicit for port 993"; README and .env.example present the
    three modes as a security ladder. Nothing asserted the property, because
    this module had no test referencing it at all (audit #2, inbound dimension -
    one of the two that crashed in the 2026-07-30 audit and never re-ran).
    """
    if cfg.tls_insecure:
        # Deliberate, auditable opt-out for an internal server with a
        # self-signed certificate. Loud, because it restores the old behaviour.
        logger.warning(
            "IMAP tls_insecure=true for host %r - the server certificate is NOT "
            "verified; credentials are exposed to anyone on the network path.",
            cfg.host,
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


@contextmanager
def open_session(cfg: ImapConfig) -> Iterator[ImapSession]:
    ctx = _tls_context(cfg)
    conn: imaplib.IMAP4
    if cfg.tls_mode == "implicit":
        conn = imaplib.IMAP4_SSL(
            cfg.host, cfg.port, ssl_context=ctx, timeout=_TIMEOUT
        )
    else:
        conn = imaplib.IMAP4(cfg.host, cfg.port, timeout=_TIMEOUT)
        if cfg.tls_mode == "starttls":
            conn.starttls(ssl_context=ctx)
    try:
        conn.login(cfg.user, cfg.password)
        yield ImapSession(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
