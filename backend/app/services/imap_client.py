"""Thin IMAP session over stdlib ``imaplib`` (v1.27.0).

Sync - the poll runs it in a worker thread (``asyncio.to_thread``). All raw IMAP
I/O lives behind ``ImapSession`` so the poll logic can be driven by a fake in
tests. No third-party dependency (``aioimaplib`` is GPLv3; avoided).
"""
from __future__ import annotations

import imaplib
import logging
from contextlib import contextmanager
from typing import Iterator

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
        typ, _ = self._c.uid("MOVE", str(uid), folder)
        if typ != "OK":
            # Server without RFC 6851 MOVE: copy then delete.
            self._c.uid("COPY", str(uid), folder)
            self.delete(uid)


@contextmanager
def open_session(cfg: ImapConfig) -> Iterator[ImapSession]:
    if cfg.tls_mode == "implicit":
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port, timeout=_TIMEOUT)
    else:
        conn = imaplib.IMAP4(cfg.host, cfg.port, timeout=_TIMEOUT)
        if cfg.tls_mode == "starttls":
            conn.starttls()
    try:
        conn.login(cfg.user, cfg.password)
        yield ImapSession(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
