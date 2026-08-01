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

from .imap_config import ImapConfig

logger = logging.getLogger("fileheron.imap")

_TIMEOUT = 30


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
        typ, data = self._c.select(mailbox)
        if typ != "OK":
            raise RuntimeError(f"IMAP SELECT {mailbox!r} failed: {typ}")
        try:
            self.message_count = int(data[0]) if data and data[0] else 0
        except (TypeError, ValueError):
            self.message_count = 0
        typ, data = self._c.response("UIDVALIDITY")
        if typ == "OK" and data and data[0]:
            try:
                return int(data[0])
            except (TypeError, ValueError):
                pass
        # Fallback via STATUS.
        typ, data = self._c.status(mailbox, "(UIDVALIDITY)")
        if typ == "OK" and data and data[0]:
            import re

            m = re.search(rb"UIDVALIDITY (\d+)", data[0])
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
        typ, data = self._c.uid("SEARCH", None, criterion)
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

    def fetch_raw(self, uid: int) -> bytes | None:
        typ, data = self._c.uid("FETCH", str(uid), "(RFC822)")
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
        self._c.expunge()

    def move(self, uid: int, folder: str) -> None:
        try:
            self._c.create(folder)  # no-op if it already exists
        except imaplib.IMAP4.error:
            pass
        try:
            typ, _ = self._c.uid("MOVE", str(uid), folder)
        except imaplib.IMAP4.error:
            # A server without RFC 6851 MOVE answers a tagged BAD, which imaplib
            # raises rather than returning; fall through to COPY+delete.
            typ = "BAD"
        if typ != "OK":
            self._c.uid("COPY", str(uid), folder)
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
