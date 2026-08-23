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
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from ..config import settings

logger = logging.getLogger("fileheron.sse")

CONNECTION_TTL_SEC = 60.0
KEEPALIVE_SEC = 15.0

# Per-worker cap on concurrent SSE connections per user. Without it, an
# authenticated user could open hundreds of streams (each holds a Redis
# pubsub subscription + an HTTP connection) and exhaust FDs / memory
# (audit finding M4). A handful of tabs is the legitimate ceiling; the
# 60s TTL recycles connections so this is per live connection, not per
# lifetime. Bounded per worker → bounded per worker's resources.
MAX_STREAMS_PER_USER = 5
_active_streams: dict[int, int] = {}

# The /admin/system stream is a second, independent long-lived connection an
# admin can hold, so it gets its own budget rather than eating into the bell's
# - and rather than being uncapped, which is what it was.
_active_admin_streams: dict[int, int] = {}


def _try_acquire(counter: dict[int, int], user_id: int) -> bool:
    n = counter.get(user_id, 0)
    if n >= MAX_STREAMS_PER_USER:
        return False
    counter[user_id] = n + 1
    return True


def _release(counter: dict[int, int], user_id: int) -> None:
    n = counter.get(user_id, 0)
    if n <= 1:
        counter.pop(user_id, None)
    else:
        counter[user_id] = n - 1


def try_acquire_user_stream(user_id: int) -> bool:
    """Reserve a stream slot for the user on this worker. Returns False
    when the user is already at MAX_STREAMS_PER_USER (caller → 429)."""
    return _try_acquire(_active_streams, user_id)


def release_user_stream(user_id: int) -> None:
    _release(_active_streams, user_id)


def try_acquire_admin_stream(user_id: int) -> bool:
    """Same cap, separate budget, for the /admin/system event stream."""
    return _try_acquire(_active_admin_streams, user_id)


def release_admin_stream(user_id: int) -> None:
    _release(_active_admin_streams, user_id)


def _channel(user_id: int) -> str:
    return f"fh:sse:{user_id}"


# Shared admin channel - every admin watching /admin/system subscribes
# to the same Redis pubsub key. Producers (cron_tracker on success
# and failure, ops_alert dispatch) publish here so the view auto-
# refreshes without polling.
_ADMIN_CHANNEL = "fh:sse:admin-system"


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
    threadpool). Failures are swallowed - SSE delivery is best-effort;
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


async def publish_admin(event: dict) -> None:
    """Fan-out to admins watching the system view. Fire-and-forget."""
    payload = json.dumps(event)
    r = _redis()
    try:
        await r.publish(_ADMIN_CHANNEL, payload)
    finally:
        await r.aclose()


def publish_admin_sync(event: dict) -> None:
    """Sync wrapper for the admin channel - mirrors `publish_sync`'s
    loop-aware behavior so callers in worker / sync contexts can both
    invoke it without ceremony."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        try:
            asyncio.run(publish_admin(event))
        except Exception:
            logger.warning("admin SSE publish failed", exc_info=True)
        return
    try:
        _track_publish_task(loop.create_task(publish_admin(event)))
    except Exception:
        logger.warning("admin SSE publish failed (loop variant)", exc_info=True)


async def stream_admin_events() -> AsyncIterator[bytes]:
    """SSE generator for `/api/admin/system/stream`. Same 60s TTL +
    keepalive shape as `stream_for_user`, just on the shared admin
    channel. Each frame is an `event:` line + a `data:` JSON line; the
    admin SPA reacts by re-fetching `getSystemStatus()` rather than
    reconstructing the table from incremental events."""
    r = _redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_ADMIN_CHANNEL)

    deadline = asyncio.get_running_loop().time() + CONNECTION_TTL_SEC
    try:
        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                yield b": close\n\n"
                return
            timeout = max(0.5, min(KEEPALIVE_SEC, deadline - now))
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
            if msg is None:
                yield b": keepalive\n\n"
                continue
            data = msg.get("data")
            if not isinstance(data, str):
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            line = f"event: {event.get('event', 'admin')}\n"
            payload = event.get("data") or event
            line += f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            yield line.encode("utf-8")
    finally:
        try:
            await pubsub.unsubscribe(_ADMIN_CHANNEL)
            await pubsub.aclose()
        except Exception:
            pass
        await r.aclose()


_CATCHUP_MAX = 100  # cap the reconnect replay so a long gap can't flood one stream


def _catchup_frames(user_id: int, last_event_id: int) -> list[tuple[bytes, int]]:
    """Notifications newer than `last_event_id` for this user, as (frame, id) pairs
    in the same shape the live loop emits. Own short-lived session (the request db
    was already released at auth). Best-effort - never raises into the stream."""
    from ..database import SessionLocal
    from ..models.notification import Notification

    out: list[tuple[bytes, int]] = []
    # SessionLocal() is INSIDE the try: it sat outside, so the one failure mode
    # the docstring promises to absorb - "never raises into the stream" - was the
    # one that propagated. A raise here does not merely skip the replay, it kills
    # a live SSE connection.
    cdb = None
    try:
        cdb = SessionLocal()
        rows = (
            cdb.query(Notification)
            .filter(Notification.user_id == user_id, Notification.id > last_event_id)
            .order_by(Notification.id.asc())
            .limit(_CATCHUP_MAX)
            .all()
        )
        for n in rows:
            payload = {
                "id": n.id,
                "category": n.category.value,
                "link_url": n.link_url,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "payload": n.payload_json,
            }
            frame = (
                "event: notification\n"
                f"id: {n.id}\n"
                f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            ).encode()
            out.append((frame, n.id))
    except Exception:
        logger.warning("SSE: catch-up replay failed for user=%s", user_id, exc_info=True)
    finally:
        if cdb is not None:
            cdb.close()
    return out


async def stream_for_user(user_id: int, last_event_id: int | None = None) -> AsyncIterator[bytes]:
    """Async generator producing SSE frames. Yields:
    - any notifications newer than `last_event_id` (reconnect catch-up), replayed
      id-tagged before the live loop
    - one keepalive comment every KEEPALIVE_SEC even if no events
    - a real `data:` frame per published event
    Closes after CONNECTION_TTL_SEC."""
    r = _redis()
    pubsub = r.pubsub()
    # Subscribe BEFORE the catch-up query so an event published during it is
    # buffered by pubsub and delivered by the live loop (deduped via seen_max).
    await pubsub.subscribe(_channel(user_id))

    seen_max = last_event_id or 0
    if last_event_id is not None:
        for frame, fid in _catchup_frames(user_id, last_event_id):
            seen_max = max(seen_max, fid)
            yield frame

    deadline = asyncio.get_running_loop().time() + CONNECTION_TTL_SEC

    try:
        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                # Tell the client to reconnect (default per the spec is
                # 3000ms - we leave that as-is).
                yield b": close\n\n"
                return
            timeout = max(0.5, min(KEEPALIVE_SEC, deadline - now))
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
            if msg is None:
                # Keepalive comment - keeps proxies from killing the connection.
                yield b": keepalive\n\n"
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
            if event_id is not None:
                if int(event_id) <= seen_max:
                    continue  # already replayed during catch-up
                seen_max = max(seen_max, int(event_id))
            payload = event.get("data") or event
            line = f"event: {event.get('event', 'notification')}\n"
            if event_id is not None:
                line += f"id: {event_id}\n"
            line += f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            yield line.encode("utf-8")
    finally:
        try:
            await pubsub.unsubscribe(_channel(user_id))
            await pubsub.aclose()
        except Exception:
            pass
        await r.aclose()
