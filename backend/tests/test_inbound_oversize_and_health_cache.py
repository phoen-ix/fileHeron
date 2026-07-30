"""Two availability findings that an outsider could trigger.

flow-inbound-8: one oversized message was materialised (raw bytes, then a
decoded payload per part, then a BytesIO for the AV stream, then a temp file)
BEFORE the 50 MB check ran, against a 512 MB worker limit. The worker is
OOM-killed - and v2.2.0's per-message try/except cannot help, because SIGKILL
raises nothing. Since the read highwater is only persisted after the loop, the
same message re-killed the worker on every poll, stopping AV scans, outbound
email and every cron. Reachable by anyone who can email the monitored mailbox.

dos-5: /api/health is anonymous and unthrottled and opened a Redis connection
plus a clamd TCP session on every single call.
"""
from __future__ import annotations

import pytest

from app.services import imap_poll as imap_poll_svc


class _FakeSession:
    def __init__(self, sizes: dict[int, int], bodies: dict[int, bytes]):
        self._sizes, self._bodies = sizes, bodies
        self.message_count = len(bodies)
        self.fetched: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_e):
        return False

    def select(self, _mailbox):
        return 1

    def search_uids_after(self, last_uid):
        return sorted(u for u in self._bodies if u > last_uid)

    def fetch_size(self, uid):
        return self._sizes.get(uid)

    def fetch_raw(self, uid):
        self.fetched.append(uid)
        return self._bodies[uid]


GOOD = b"""From: a@b.c
To: inbox@example.com
Subject: fine
Message-ID: <ok@example.com>

body
"""


@pytest.fixture
def imap_enabled(db, monkeypatch):
    monkeypatch.setattr(imap_poll_svc.imap_config, "is_enabled", lambda _db: True)

    class _Cfg:
        is_configured = True
        mailbox = "INBOX"

    monkeypatch.setattr(imap_poll_svc.imap_config, "resolve_imap_config", lambda _db: _Cfg())
    monkeypatch.setattr(imap_poll_svc.imap_config, "post_fetch_action", lambda _db: "none")
    monkeypatch.setattr(imap_poll_svc.imap_config, "move_folder", lambda _db: "")
    monkeypatch.setattr(imap_poll_svc.inbound_mail, "ingest", lambda *a, **k: object())
    monkeypatch.setattr(imap_poll_svc.inbound_mail, "ingested_by_uid", lambda *a, **k: False)


def test_oversize_message_is_never_fetched(db, imap_enabled):
    """The whole point: it must be skipped WITHOUT downloading it, because
    downloading it is what kills the worker."""
    huge = imap_poll_svc.MAX_MESSAGE_BYTES + 1
    session = _FakeSession({1: huge, 2: len(GOOD)}, {1: b"", 2: GOOD})

    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _c: session
    )

    assert 1 not in session.fetched, "the oversize message was downloaded anyway"
    assert result["skipped"] == 1
    assert result["ingested"] == 1


def test_highwater_advances_past_an_oversize_message(db, imap_enabled):
    """Without this the same message is re-selected every poll - the re-kill
    loop that makes the outage permanent."""
    huge = imap_poll_svc.MAX_MESSAGE_BYTES + 1
    session = _FakeSession({5: huge}, {5: b""})

    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _c: session
    )

    assert result["ok"] is True
    assert result["last_uid"] == 5


def test_server_under_reporting_size_is_still_caught(db, imap_enabled):
    """Belt and braces: a server that lies about (or omits) RFC822.SIZE must not
    defeat the guard."""
    body = b"x" * (imap_poll_svc.MAX_MESSAGE_BYTES + 10)
    session = _FakeSession({7: 10}, {7: body})  # claims 10 bytes, sends 64 MB+

    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _c: session
    )

    assert result["skipped"] == 1
    assert result["ingested"] == 0
    assert result["last_uid"] == 7


def test_normal_messages_are_unaffected(db, imap_enabled):
    """Control: the guard must not start dropping ordinary mail."""
    session = _FakeSession({3: len(GOOD)}, {3: GOOD})
    result = imap_poll_svc.run_poll(
        manual=False, db=db, session_opener=lambda _c: session
    )
    assert result["skipped"] == 0
    assert result["ingested"] == 1
    assert 3 in session.fetched


# --- dos-5 -----------------------------------------------------------------


def test_health_probes_are_cached(monkeypatch):
    """A burst of anonymous health checks must not become a burst of Redis and
    clamd connections."""
    from app.routers import health as health_mod

    calls = {"redis": 0}

    class _R:
        def ping(self):
            calls["redis"] += 1

    monkeypatch.setattr(health_mod, "_probe_cache", None, raising=False)
    monkeypatch.setattr("app.redis_client.get_redis", lambda: _R())
    monkeypatch.setattr(health_mod.settings, "AV_SKIP", True)

    for _ in range(25):
        health_mod._cached_dependency_probes()

    assert calls["redis"] == 1, f"probed the dependency {calls['redis']} times, expected 1"


def test_health_cache_expires(monkeypatch):
    """Control: an outage must still surface promptly - a cache that never
    expires would report a dead dependency as healthy forever."""
    from app.routers import health as health_mod

    calls = {"redis": 0}

    class _R:
        def ping(self):
            calls["redis"] += 1

    monkeypatch.setattr(health_mod, "_probe_cache", None, raising=False)
    monkeypatch.setattr("app.redis_client.get_redis", lambda: _R())
    monkeypatch.setattr(health_mod.settings, "AV_SKIP", True)

    health_mod._cached_dependency_probes()
    # Age the cache past its TTL.
    ts, val = health_mod._probe_cache
    health_mod._probe_cache = (ts - health_mod._PROBE_CACHE_TTL_SEC - 1, val)
    health_mod._cached_dependency_probes()

    assert calls["redis"] == 2


def test_health_cache_ttl_is_short():
    """The window must stay small enough that a real outage is not masked."""
    from app.routers import health as health_mod

    assert health_mod._PROBE_CACHE_TTL_SEC <= 30
