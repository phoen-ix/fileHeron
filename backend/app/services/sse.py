"""SSE (Server-Sent Events) over Redis pubsub.

Each user gets their own pubsub channel `fh:sse:<user_id>`. Producers
(notification dispatcher) call `publish(user_id, event)`. Consumers
(GET /api/notifications/stream) hold an HTTP connection and forward
events as SSE frames until either the client disconnects or 60 seconds
elapse, after which the client is expected to reconnect.

Why 60s and not infinite: SSE behind reverse proxies (Traefik) often
hits idle timeouts that close the connection unpredictably. A
deterministic 60s window means the client knows when to reconnect and
never has to guess.

The event ID is the notifications.id BIGINT, exposed as the SSE
`Last-Event-Id` header. On reconnect the client sends it back; the
server emits any rows newer than that ID before going back to live
streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

from ..config import settings

logger = logging.getLogger("fileheron.sse")

CONNECTION_TTL_SEC = 60.0
KEEPALIVE_SEC = 15.0


def _channel(user_id: int) -> str:
    return f"fh:sse:{user_id}"


def _redis() -> aioredis.Redis:
    return aioredis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True
    )


# Strong references for fire-and-forget tasks scheduled by
# `publish_sync`. asyncio doesn't keep a reference to tasks created
# via `loop.create_task()`, so the GC could collect one mid-execution
# and inject GeneratorExit into the running coroutine. We park the
# task here and discard on completion.
_pending_publish_tasks: set[asyncio.Task] = set()


def _track_publish_task(task: asyncio.Task) -> None:
    _pending_publish_tasks.add(task)
    task.add_done_callback(_pending_publish_tasks.discard)


async def publish(user_id: int, event: dict) -> None:
    """Fire-and-forget push to a user's channel. No-op if no listener
    (Redis pubsub doesn't queue)."""
    payload = json.dumps(event)
    r = _redis()
    try:
        await r.publish(_channel(user_id), payload)
    finally:
        await r.aclose()


def publish_sync(user_id: int, event: dict) -> None:
    """Sync wrapper for callers in non-async contexts (the notification
    dispatcher, called from sync FastAPI routes that run in the
    threadpool). Failures are swallowed — SSE delivery is best-effort;
    the in-app row is the durable record."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        try:
            asyncio.run(publish(user_id, event))
        except Exception:
            logger.warning(
                "SSE publish failed for user_id=%s", user_id, exc_info=True
            )
        return
    # Already inside an event loop (tests, async route): schedule and
    # forget. The task is fire-and-forget; we don't await it, but we
    # do hold a strong reference (see `_pending_publish_tasks` above)
    # so the GC doesn't kill it mid-flight.
    try:
        _track_publish_task(loop.create_task(publish(user_id, event)))
    except Exception:
        logger.warning(
            "SSE publish (loop variant) failed for user_id=%s",
            user_id,
            exc_info=True,
        )


async def stream_for_user(user_id: int, last_event_id: int | None = None) -> AsyncIterator[bytes]:
    """Async generator producing SSE frames. Yields:
    - one keepalive comment every KEEPALIVE_SEC even if no events
    - a real `data:` frame per published event
    Closes after CONNECTION_TTL_SEC.

    If `last_event_id` is set, the caller should have flushed any
    catch-up frames before this generator starts (we don't double-fetch
    here)."""
    r = _redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_channel(user_id))

    deadline = asyncio.get_event_loop().time() + CONNECTION_TTL_SEC
    last_yield = asyncio.get_event_loop().time()

    try:
        while True:
            now = asyncio.get_event_loop().time()
            if now >= deadline:
                # Tell the client to reconnect (default per the spec is
                # 3000ms — we leave that as-is).
                yield b": close\n\n"
                return
            timeout = max(0.5, min(KEEPALIVE_SEC, deadline - now))
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
            if msg is None:
                # Keepalive comment — keeps proxies from killing the connection.
                yield b": keepalive\n\n"
                last_yield = now
                continue

            data = msg.get("data")
            if not isinstance(data, str):
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("SSE: dropped malformed pubsub payload")
                continue

            event_id = event.get("id")
            payload = event.get("data") or event
            line = f"event: {event.get('event', 'notification')}\n"
            if event_id is not None:
                line += f"id: {event_id}\n"
            line += f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            yield line.encode("utf-8")
            last_yield = now
    finally:
        try:
            await pubsub.unsubscribe(_channel(user_id))
            await pubsub.aclose()
        except Exception:
            pass
        await r.aclose()
