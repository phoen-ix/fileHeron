"""Two small IMAP robustness fixes (audit 2026-11-08).

- The cleartext warning for `tls_mode=none` against a remote host in production
  was logged as an error on EVERY resolve - each poll tick and each settings
  GET - so it was most of the log. It is logged once per process and host.
- A server that refuses STARTTLS raises `imaplib.IMAP4.error`, not an OSError,
  and the plaintext socket was left open behind it.
"""
from __future__ import annotations

import imaplib
import logging

import pytest

from app.services import imap_client, imap_config


def test_the_cleartext_warning_is_logged_once_per_host(db, monkeypatch, caplog):
    from app.config import settings
    from app.services import settings as s

    monkeypatch.setattr(imap_config, "_CLEARTEXT_WARNED", set())
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.remote.example", actor=None)
    s.set_value(db, key=s.Keys.IMAP_TLS_MODE, value="none", actor=None)
    db.commit()

    with caplog.at_level(logging.ERROR, logger="fileheron.imap_config"):
        for _ in range(3):
            imap_config.resolve_imap_config(db)

    assert len([r for r in caplog.records if "CLEARTEXT" in r.getMessage()]) == 1


def test_a_refused_starttls_closes_the_socket(monkeypatch):
    closed: list[bool] = []
    imap_error = imaplib.IMAP4.error  # captured before IMAP4 is replaced below

    class _Conn:
        def __init__(self, *a, **k):
            pass

        def starttls(self, **_k):
            raise imap_error("STARTTLS not supported")

        def shutdown(self):
            closed.append(True)

    monkeypatch.setattr(imap_client.imaplib, "IMAP4", _Conn)
    cfg = imap_config.ImapConfig(
        host="imap.example", port=143, user="u", password="p",
        tls_mode="starttls", mailbox="INBOX", tls_insecure=False,
    )

    with pytest.raises(imap_error), imap_client.open_session(cfg):
        pass

    assert closed == [True]
