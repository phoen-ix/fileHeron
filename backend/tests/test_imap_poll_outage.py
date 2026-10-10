"""One missed IMAP cycle is not an outage (v2.25.2).

The poll raised - cron_failed, an ops_alert and error-alert mail to every admin -
on any failure. Twice in thirty days on the reference instance that was a single
blip followed by a clean poll five minutes later (2026-09-18 12:47, a 54 ms
failure; 2026-10-07 20:14, "Network is unreachable"). A failure to REACH the
server now alerts only once the mailbox has not answered for TRANSIENT_GRACE;
August's hours-long outages still alert, and so does anything that will not fix
itself (a refused login, a TLS fault, a name that does not exist).
"""
from __future__ import annotations

import contextlib
import errno
import imaplib
import socket
import ssl
from datetime import datetime, timedelta

import pytest

from app.services import imap_client
from app.services import imap_poll as svc
from app.services import settings as s
from app.services.imap_config import ImapConfig
from app.utils.timeutil import utc_now


@pytest.fixture
def connect_failure(monkeypatch):
    """imap_client._connect_failure, the shape open_session really raises - with
    the address lookup it adds to the message kept off the network."""

    def _no_dns(*_a, **_k):
        raise OSError("no lookups in tests")

    monkeypatch.setattr(imap_client.socket, "getaddrinfo", _no_dns)
    cfg = ImapConfig(
        host="mail.example.com", port=993, user="u", password="p",
        tls_mode="implicit", mailbox="INBOX",
    )

    def wrap(original: OSError) -> OSError:
        try:
            raise imap_client._connect_failure(cfg, original) from original
        except OSError as wrapped:
            return wrapped

    return wrap


@pytest.mark.parametrize(
    "original",
    [
        OSError(errno.ENETUNREACH, "Network is unreachable"),
        OSError(errno.EHOSTUNREACH, "No route to host"),
        ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
        TimeoutError("timed out"),
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
    ],
)
def test_failing_to_reach_the_server_is_transient(original, connect_failure):
    assert svc.is_transient(connect_failure(original)) is True


@pytest.mark.parametrize(
    "exc",
    [
        imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Authentication failed."),
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
        ValueError("mailbox name"),
    ],
)
def test_what_will_not_fix_itself_is_not_transient(exc):
    assert svc.is_transient(exc) is False


def test_a_name_that_does_not_exist_is_a_configuration_error(connect_failure):
    nxdomain = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    assert svc.is_transient(connect_failure(nxdomain)) is False


def test_a_connection_dropped_mid_session_is_transient():
    assert svc.is_transient(imaplib.IMAP4.abort("socket error: EOF")) is True


NOW = datetime(2026, 10, 7, 20, 14)
BLIP = {"ok": False, "error": "OSError: [Errno 101] Network is unreachable", "transient": True}


@pytest.mark.parametrize(
    ("result", "last_ok", "outage"),
    [
        (BLIP, NOW - timedelta(minutes=5), False),  # one missed cycle
        (BLIP, NOW - svc.TRANSIENT_GRACE, False),
        (BLIP, NOW - svc.TRANSIENT_GRACE - timedelta(seconds=1), True),
        (BLIP, None, True),  # never answered: nothing to give grace to
        ({**BLIP, "transient": False}, NOW - timedelta(minutes=1), True),
        ({"ok": False, "error": "not_configured"}, None, False),
        ({"ok": True, "fetched": 0}, None, False),
    ],
)
def test_is_outage(result, last_ok, outage):
    assert svc.is_outage(result, last_success_at=last_ok, now=NOW) is outage


def _opener_raising(exc):
    @contextlib.contextmanager
    def _cm(_cfg):
        raise exc
        yield  # pragma: no cover

    return _cm


def _enable(db):
    s.set_value(db, key=s.Keys.IMAP_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.example.com", actor=None)
    db.commit()


def test_run_poll_reports_whether_a_failure_is_transient(db, connect_failure):
    _enable(db)
    unreachable = connect_failure(OSError(errno.ENETUNREACH, "Network is unreachable"))
    out = svc.run_poll(manual=False, db=db, session_opener=_opener_raising(unreachable))
    assert out["ok"] is False and out["transient"] is True
    login = imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Authentication failed.")
    out = svc.run_poll(manual=False, db=db, session_opener=_opener_raising(login))
    assert out["ok"] is False and out["transient"] is False


@pytest.mark.asyncio
async def test_the_cron_lets_one_blip_pass_and_alerts_on_an_outage(db, monkeypatch):
    from app.workers import imap_poll as worker

    monkeypatch.setattr(svc, "run_poll", lambda **_k: dict(BLIP))
    job = worker.imap_poll.__wrapped__  # the body, without @track_cron's bookkeeping

    s.set_value(
        db, key=s.Keys.IMAP_LAST_SUCCESS_AT,
        value=(utc_now() - timedelta(minutes=5)).isoformat(), actor=None,
    )
    db.commit()
    assert (await job({}))["transient"] is True  # returned, not raised

    s.set_value(
        db, key=s.Keys.IMAP_LAST_SUCCESS_AT,
        value=(utc_now() - timedelta(minutes=20)).isoformat(), actor=None,
    )
    db.commit()
    with pytest.raises(worker.InboundPollError):
        await job({})
