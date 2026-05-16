"""SSE pubsub fanout: channel naming + publish + sync wrapper error swallow.

The 60s TTL reconnect behavior is integration-shaped and isn't covered
here — it would need a real Redis subscriber. These tests cover the
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
            return 0  # zero subscribers — pubsub is fire-and-forget

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
    caller (notification dispatcher) — the durable in-app row already
    landed by the time we get here."""

    class _Boom:
        async def publish(self, *_a, **_kw):
            raise ConnectionError("nope")

        async def aclose(self):
            pass

    monkeypatch.setattr(sse_svc, "_redis", lambda: _Boom())
    # Also restore the real publish — publish_sync calls publish internally
    # via asyncio.run / loop.create_task; the conftest noop would mask the
    # error-handling we want to exercise.
    monkeypatch.setattr(sse_svc, "publish", _REAL_PUBLISH)

    # No event loop running — publish_sync uses asyncio.run internally.
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
