"""Lightweight wrapper around ARQ's `enqueue_job`.

Two entry points:

- ``await aenqueue(name, ...)`` - for async callers (FastAPI ``async
  def`` routes, ARQ jobs). Awaits the Redis push so failure surfaces
  inline.
- ``enqueue(name, ...)`` - fire-and-forget for sync callers (FastAPI
  sync routes that run in a threadpool, cron tasks). When called from
  an async context it schedules the work on the running loop with a
  done-callback so transient Redis failures still get logged.

Why both: a fresh ARQ pool is opened, the job pushed, then the pool
closed - cheap because it's a single Redis round-trip. Mixing
``asyncio.run`` into an existing event loop raises ``RuntimeError``
and silently drops the enqueue, so we can't lazily wrap async with
sync. Hence the dual API.

``enqueue_many(jobs)`` / ``aenqueue_many(jobs)`` exist because "cheap
because it's a single round-trip" stops being true N times over: a
fan-out that pushed one job per recipient built and tore down a pool
per recipient, serially, on the request thread. Use them wherever the
count scales with anything a user controls.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from ..config import settings

logger = logging.getLogger("fileheron.job_queue")


async def aenqueue(name: str, *args: Any, **kwargs: Any) -> None:
    """Async-context enqueue. Always prefer this from ``async def``
    routes - failures surface inline and the request handler can react
    if needed."""
    pool = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    try:
        await pool.enqueue_job(name, *args, _queue_name="fileheron:default", **kwargs)
    finally:
        await pool.aclose()


async def aenqueue_many(jobs: list[tuple[str, tuple, dict]]) -> None:
    """Push a batch of jobs over ONE pool, concurrently."""
    if not jobs:
        return
    pool = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    try:
        await asyncio.gather(
            *(
                pool.enqueue_job(name, *args, _queue_name="fileheron:default", **kwargs)
                for name, args, kwargs in jobs
            )
        )
    finally:
        await pool.aclose()


# Enqueue args routinely carry secrets: `send_email_job` is called with the
# fully rendered `text_body`/`html_body` and a `list_unsubscribe` header
# embedding a 180-day manage-subscriptions token. Logging `%r` of args and
# kwargs on a Redis outage therefore wrote the whole email, the recipient
# address and a live credential to container stdout - which is json-file
# logged, rotated at 50-100 MB x3, and readable by anyone with the host
# (audit 2026-07-30). Log the shape, never the values.
_SAFE_KWARG_KEYS = frozenset({"email_log_id", "file_id", "share_id", "user_id", "webhook_id"})


def _redact(args: Any, kwargs: Any) -> str:
    """Positional arity plus the kwarg key set, with only known-harmless ids
    given their value. Enough to identify which job failed; never enough to
    leak what it carried."""
    shown = {k: kwargs[k] for k in sorted(kwargs) if k in _SAFE_KWARG_KEYS}
    withheld = sorted(k for k in kwargs if k not in _SAFE_KWARG_KEYS)
    parts = [f"{len(args)} positional"]
    if shown:
        parts.append(", ".join(f"{k}={v!r}" for k, v in shown.items()))
    if withheld:
        parts.append(f"withheld={withheld}")
    return "; ".join(parts)


def _log_task_failure(name: str, args: Any, kwargs: Any):
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "failed to enqueue %s [%s]: %s", name, _redact(args, kwargs), exc
            )
    return _on_done


def enqueue(name: str, *args: Any, **kwargs: Any) -> None:
    """Sync-context enqueue. Failures are logged but don't propagate -
    a missed scan is better than a failed upload.

    If invoked from inside a running event loop (e.g. an ``async def``
    route forgot to use ``aenqueue``), schedules the work on the
    current loop instead of raising ``RuntimeError`` from
    ``asyncio.run``."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        try:
            asyncio.run(aenqueue(name, *args, **kwargs))
        except Exception as e:
            logger.error("failed to enqueue %s [%s]: %s", name, _redact(args, kwargs), e)
        return

    task = loop.create_task(aenqueue(name, *args, **kwargs))
    task.add_done_callback(_log_task_failure(name, args, kwargs))


def enqueue_many(jobs: list[tuple[str, tuple, dict]]) -> None:
    """Sync-context batch enqueue: one event loop, one Redis pool, N jobs.

    `enqueue` opens and closes a pool per call, which is cheap once and
    expensive N times. A share to twenty people fanned out twenty
    `asyncio.run` calls, each building and tearing down its own connection
    pool, serially, on the request thread while the user waited (audit
    2026-07-30, dos-15). This is the same work with one connection.

    Same failure posture as `enqueue`: logged, never raised - a missed
    notification email must not fail the action that produced it. `gather`
    means one failing push aborts the rest of the batch, which matches the
    single-job behaviour on a Redis outage (nothing gets through) rather than
    introducing a partial-success mode nothing downstream expects.
    """
    if not jobs:
        return
    shape = f"{len(jobs)} jobs: " + ", ".join(
        f"{name}[{_redact(args, kwargs)}]" for name, args, kwargs in jobs[:3]
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        try:
            asyncio.run(aenqueue_many(jobs))
        except Exception as e:
            logger.error("failed to enqueue batch [%s]: %s", shape, e)
        return

    task = loop.create_task(aenqueue_many(jobs))

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("failed to enqueue batch [%s]: %s", shape, exc)

    task.add_done_callback(_on_done)
