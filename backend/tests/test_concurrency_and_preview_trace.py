"""Two runs of the same job, and a route that served bytes without a trace.

flow-inbound-6  `fetch-now` runs the poll inline in the request while the
                five-minute cron can enter the same function concurrently in
                the worker. Both then read the same `last_uid`, fetch the same
                messages and race on the dedup insert - and with a post-fetch
                action of delete or move, the loser can apply that action to a
                message the winner is mid-ingest on.

flow-publiclink-2  the public preview route is free of charge by design: it does
                not decrement the download budget. But it hands an anonymous
                caller the COMPLETE original bytes, and nothing recorded that -
                no download_log row, no audit entry. A share could be
                exfiltrated in full through preview while the download counter
                still read zero and neither the owner nor an investigator would
                find any trace it had left the server.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.models.audit_log import AuditEventType
from app.services import imap_poll

# --- flow-inbound-6 ---------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


def test_a_second_poll_is_refused_while_one_holds_the_mailbox(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(imap_poll, "get_redis", lambda: fake)

    first = imap_poll._acquire_poll_lock()
    assert first is not None
    assert imap_poll._acquire_poll_lock() is None, (
        "two polls can still read the same last_uid and fetch the same messages"
    )


def test_releasing_lets_the_next_run_in(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(imap_poll, "get_redis", lambda: fake)

    token = imap_poll._acquire_poll_lock()
    imap_poll._release_poll_lock(token)
    assert imap_poll._acquire_poll_lock() is not None


def test_an_overrunning_run_cannot_release_its_successors_claim(monkeypatch):
    """The TTL exists so a killed worker frees the mailbox. A slow run that
    outlives its own TTL must not then delete the lock the next run took."""
    fake = _FakeRedis()
    monkeypatch.setattr(imap_poll, "get_redis", lambda: fake)

    stale = imap_poll._acquire_poll_lock()
    fake.store.clear()  # TTL expired
    successor = imap_poll._acquire_poll_lock()

    imap_poll._release_poll_lock(stale)
    assert fake.get(imap_poll._POLL_LOCK_KEY) == successor, (
        "the overrunning run deleted the successor's claim"
    )


def test_redis_being_down_does_not_stop_ingestion(monkeypatch):
    """Deliberate: the window this closes is narrow, and no poll at all means
    the mailbox backs up and the highwater never advances."""
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(imap_poll, "get_redis", _boom)
    assert imap_poll._acquire_poll_lock() is not None


def test_the_lock_is_taken_inside_run_poll_not_the_router():
    """Guarding only `fetch-now` would leave the cron-versus-cron overlap
    unguarded, and ARQ enqueues without a job id so nothing else deduplicates
    it."""
    src = inspect.getsource(imap_poll.run_poll)
    assert "_acquire_poll_lock()" in src
    assert "_release_poll_lock" in src


def test_the_skip_is_reported_rather_than_silent():
    src = inspect.getsource(imap_poll.run_poll)
    assert '"skipped": "already_running"' in src


# --- flow-publiclink-2 ------------------------------------------------------


def test_a_preview_audit_event_exists():
    assert hasattr(AuditEventType, "public_link_previewed")


def test_the_preview_route_records_the_transfer():
    from app.routers import public as public_router

    src = inspect.getsource(public_router)
    idx = src.index("public preview: storage missing")
    window = src[idx : idx + 1400]
    assert "public_link_previewed" in window, (
        "an anonymous caller can still take the full bytes with no trace"
    )


def test_range_continuations_do_not_write_a_row_each():
    """A PDF viewer fetches in chunks; one row per chunk would bury the signal
    the row exists to provide."""
    from app.routers import public as public_router

    src = inspect.getsource(public_router)
    idx = src.index("public preview: storage missing")
    window = src[idx : idx + 1400]
    assert "is_partial_continuation(request)" in window


@pytest.mark.parametrize("field", ["file_id", "share_id", "bytes"])
def test_the_row_says_what_left(field):
    from app.routers import public as public_router

    src = inspect.getsource(public_router)
    idx = src.index("public_link_previewed")
    assert field in src[idx : idx + 600]
