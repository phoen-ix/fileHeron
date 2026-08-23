"""SSE pubsub fanout: channel naming + publish + sync wrapper error swallow.

The 60s TTL reconnect behavior is integration-shaped and isn't covered
here - it would need a real Redis subscriber. These tests cover the
producer-side invariants the dispatcher relies on.

Note: conftest has an autouse `_no_op_sse_publish` that replaces
`sse.publish` / `sse.publish_sync` so other tests don't fire real
network traffic. We capture references to the *real* implementations
at module-load time, before any fixture runs, so this file can still
exercise them against mocked Redis.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import sse as sse_svc

# Captured before the conftest autouse monkeypatch swaps these out.
_REAL_PUBLISH = sse_svc.publish
_REAL_PUBLISH_SYNC = sse_svc.publish_sync


def test_channel_naming():
    assert sse_svc._channel(42) == "fh:sse:42"
    assert sse_svc._channel(0) == "fh:sse:0"


@pytest.mark.asyncio
async def test_publish_sends_to_user_channel(monkeypatch):
    sent: list[tuple[str, str]] = []

    class _FakeRedis:
        async def publish(self, channel, payload):
            sent.append((channel, payload))
            return 0  # zero subscribers - pubsub is fire-and-forget

        async def aclose(self):
            pass

    monkeypatch.setattr(sse_svc, "_redis", lambda: _FakeRedis())

    await _REAL_PUBLISH(7, {"event": "test", "data": {"n": 1}})

    assert len(sent) == 1
    channel, payload = sent[0]
    assert channel == "fh:sse:7"
    assert '"event"' in payload and '"data"' in payload


@pytest.mark.asyncio
async def test_publish_channel_isolation(monkeypatch):
    sent: list[str] = []

    class _FakeRedis:
        async def publish(self, channel, _payload):
            sent.append(channel)
            return 0

        async def aclose(self):
            pass

    monkeypatch.setattr(sse_svc, "_redis", lambda: _FakeRedis())

    await _REAL_PUBLISH(1, {"event": "x"})
    await _REAL_PUBLISH(2, {"event": "x"})
    await _REAL_PUBLISH(1, {"event": "y"})

    # Each publish targets a single user's channel; no cross-user leak.
    assert sent == ["fh:sse:1", "fh:sse:2", "fh:sse:1"]


def test_publish_sync_swallows_redis_down(monkeypatch):
    """SSE is best-effort. publish_sync must never raise back to the
    caller (notification dispatcher) - the durable in-app row already
    landed by the time we get here."""

    class _Boom:
        async def publish(self, *_a, **_kw):
            raise ConnectionError("nope")

        async def aclose(self):
            pass

    monkeypatch.setattr(sse_svc, "_redis", lambda: _Boom())
    # Also restore the real publish - publish_sync calls publish internally
    # via asyncio.run / loop.create_task; the conftest noop would mask the
    # error-handling we want to exercise.
    monkeypatch.setattr(sse_svc, "publish", _REAL_PUBLISH)

    # No event loop running - publish_sync uses asyncio.run internally.
    _REAL_PUBLISH_SYNC(1, {"event": "x"})  # must not raise


@pytest.mark.asyncio
async def test_publish_sync_inside_running_loop_does_not_raise(monkeypatch):
    """When called from within an event loop, publish_sync schedules
    the task and returns. We hold a strong reference in
    `_pending_publish_tasks` so the GC doesn't reap mid-flight."""

    class _FakeRedis:
        async def publish(self, *_a, **_kw):
            return 0

        async def aclose(self):
            pass

    monkeypatch.setattr(sse_svc, "_redis", lambda: _FakeRedis())
    monkeypatch.setattr(sse_svc, "publish", _REAL_PUBLISH)

    _REAL_PUBLISH_SYNC(99, {"event": "in-loop"})
    # Yield so the scheduled task can run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# --- reconnect catch-up -----------------------------------------------------
#
# This file's own docstring says the TTL/reconnect behaviour "isn't covered
# here - it would need a real Redis subscriber". `_catchup_frames` needs no
# subscriber at all, and `stream_for_user` needs only a stubbed `_redis`. Both
# were untested, which left the `_CATCHUP_MAX` replay cap - the bound that stops
# one long-disconnected tab flooding its own stream on reconnect - asserted
# nowhere.


def _notif(db, user, *, body="x"):
    from app.models.notification import Notification, NotificationCategory

    n = Notification(
        user_id=user.id,
        category=NotificationCategory.share_created,
        payload_json={"body": body},
    )
    db.add(n)
    db.flush()
    return n


def test_catchup_returns_only_events_newer_than_the_last_seen(db, make_user):
    u = make_user(email="catchup@test.local")
    first = _notif(db, u)
    second = _notif(db, u)
    db.commit()

    frames = sse_svc._catchup_frames(u.id, first.id)
    assert [fid for _f, fid in frames] == [second.id]


def test_catchup_is_scoped_to_the_user(db, make_user):
    mine = make_user(email="mine@test.local")
    theirs = make_user(email="theirs@test.local")
    _notif(db, theirs)
    db.commit()
    assert sse_svc._catchup_frames(mine.id, 0) == []


def test_catchup_is_capped(db, make_user):
    """A tab offline for a day must not replay a day of notifications in one
    burst on reconnect."""
    u = make_user(email="flood@test.local")
    for _ in range(sse_svc._CATCHUP_MAX + 25):
        _notif(db, u)
    db.commit()
    assert len(sse_svc._catchup_frames(u.id, 0)) == sse_svc._CATCHUP_MAX


def test_catchup_is_ordered_oldest_first(db, make_user):
    u = make_user(email="order@test.local")
    ids = [_notif(db, u).id for _ in range(5)]
    db.commit()
    assert [fid for _f, fid in sse_svc._catchup_frames(u.id, 0)] == ids


def test_catchup_never_raises_into_the_stream(monkeypatch, make_user, db):
    """Documented as best-effort: an exception here would kill a live stream
    rather than merely skipping the replay."""
    u = make_user(email="boom@test.local")
    db.commit()

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr("app.database.SessionLocal", _boom)
    assert sse_svc._catchup_frames(u.id, 0) == []


@pytest.mark.asyncio
async def test_the_stream_replays_then_closes(db, make_user, monkeypatch):
    """Catch-up frames come out BEFORE the live loop, and the connection ends
    with the `: close` frame that drives the client's deterministic reconnect
    (the 60s lifetime is by design - see CLAUDE.md)."""
    u = make_user(email="stream@test.local")
    n = _notif(db, u)
    db.commit()

    class _FakePubSub:
        async def subscribe(self, _ch):
            return None

        async def get_message(self, **_kw):
            return None

        async def unsubscribe(self, *_a):
            return None

        async def aclose(self):
            return None

    class _FakeRedis:
        def pubsub(self):
            return _FakePubSub()

        async def aclose(self):
            return None

    monkeypatch.setattr(sse_svc, "_redis", lambda: _FakeRedis())
    monkeypatch.setattr(sse_svc, "CONNECTION_TTL_SEC", 0.0)

    frames = [f async for f in sse_svc.stream_for_user(u.id, last_event_id=n.id - 1)]
    assert any(str(n.id).encode() in f for f in frames), "the replay never arrived"
    assert frames[-1] == b": close\n\n"
