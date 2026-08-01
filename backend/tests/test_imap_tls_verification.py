"""IMAP TLS must authenticate the server.

From audit #2, in the `inbound` dimension - one of the two that crashed during
the 2026-07-30 audit and never re-ran.

`imaplib.IMAP4_SSL(host, port)` and `IMAP4.starttls()` with no `ssl_context`
both fall back to `ssl._create_stdlib_context()`, an alias for
`_create_unverified_context`: `verify_mode=CERT_NONE`, `check_hostname=False`,
no CA store. Verified inside the deployed image (Python 3.14.6):

    stdlib ctx  : verify_mode=0 check_hostname=False
    default ctx : verify_mode=2 check_hostname=True

So BOTH modes this product presents as the secure ones accepted any certificate
for any hostname. What that exposes is not an isolated mailbox password -
`imap_config.uses_smtp_credentials` defaults to True, so the LOGIN carries the
SMTP credentials, i.e. the account this instance sends all outbound mail from.

Every piece of prose around it asserted the opposite: `imap_config` logs an
error only for `tls_mode='none'` "because that is almost certainly a mistake",
framing implicit/starttls as safe; the poll's error text tells admins to "use
implicit for port 993"; README and .env.example present the three modes as a
security ladder. Nothing had ever asserted the property, because
`services/imap_client.py` had no test referencing it at all.
"""
from __future__ import annotations

import ssl

import pytest

from app.services import imap_client
from app.services.imap_config import ImapConfig


def _cfg(**kw) -> ImapConfig:
    base = {
        "host": "imap.example.invalid",
        "port": 993,
        "user": "fh@example.invalid",
        "password": "secret",
        "tls_mode": "implicit",
        "mailbox": "INBOX",
    }
    base.update(kw)
    return ImapConfig(**base)


def test_the_default_context_verifies_the_certificate_and_hostname():
    ctx = imap_client._tls_context(_cfg())
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_the_stdlib_default_this_replaces_verifies_nothing():
    """The control that gives the assertion above its meaning: what imaplib
    would have used, and did use, on every connection this product made."""
    stdlib = ssl._create_stdlib_context()
    assert stdlib.verify_mode == ssl.CERT_NONE
    assert stdlib.check_hostname is False


@pytest.mark.parametrize("mode", ["implicit", "starttls"])
def test_both_tls_modes_are_given_a_verified_context(monkeypatch, mode):
    """The bug was not in the context - it was that no context was passed. Both
    call sites must hand one over, or imaplib silently substitutes the
    unverified default."""
    seen: dict = {}

    class _Conn:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            seen["implicit_ctx"] = ssl_context

        def starttls(self, ssl_context=None):
            seen["starttls_ctx"] = ssl_context

        def login(self, u, p):
            seen["logged_in"] = True

        def logout(self):
            pass

    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", _Conn)
    monkeypatch.setattr(imap_client.imaplib, "IMAP4", _Conn)
    monkeypatch.setattr(imap_client, "ImapSession", lambda c: c)

    with imap_client.open_session(_cfg(tls_mode=mode)):
        pass

    key = "implicit_ctx" if mode == "implicit" else "starttls_ctx"
    ctx = seen.get(key)
    assert ctx is not None, (
        f"{mode} passed no ssl_context, so imaplib substitutes an unverified "
        "one and the server certificate is never checked"
    )
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_the_insecure_opt_out_is_explicit_and_loud(caplog):
    """An internal server with a self-signed certificate is a real deployment,
    so the unsafe path still exists - but as a deliberate, auditable choice
    rather than the silent default it used to be."""
    import logging

    with caplog.at_level(logging.WARNING):
        ctx = imap_client._tls_context(_cfg(tls_insecure=True))
    assert ctx.verify_mode == ssl.CERT_NONE
    assert "not verified" in caplog.text.lower()


def test_the_opt_out_defaults_off(db):
    """A deployment that says nothing gets verification."""
    from app.services import imap_config

    cfg = imap_config.resolve_imap_config(db)
    assert cfg.tls_insecure is False
