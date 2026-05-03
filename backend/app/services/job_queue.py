"""Lightweight wrapper around ARQ's `enqueue_job`.

Two entry points:

- ``await aenqueue(name, ...)`` — for async callers (FastAPI ``async
  def`` routes, ARQ jobs). Awaits the Redis push so failure surfaces
  inline.
- ``enqueue(name, ...)`` — fire-and-forget for sync callers (FastAPI
  sync routes that run in a threadpool, cron tasks). When called from
  an async context it schedules the work on the running loop with a
  done-callback so transient Redis failures still get logged.

Why both: a fresh ARQ pool is opened, the job pushed, then the pool
closed — cheap because it's a single Redis round-trip. Mixing
``asyncio.run`` into an existing event loop raises ``RuntimeError``
and silently drops the enqueue, so we can't lazily wrap async with
sync. Hence the dual API.
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
    routes — failures surface inline and the request handler can react
    if needed."""
    pool = await create_pool(
        RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    )
    try:
        await pool.enqueue_job(name, *args, _queue_name="fileheron:default", **kwargs)
    finally:
        await pool.aclose()


def _log_task_failure(name: str, args: Any, kwargs: Any):
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "failed to enqueue %s(%r,%r): %s", name, args, kwargs, exc
            )
    return _on_done


def enqueue(name: str, *args: Any, **kwargs: Any) -> None:
    """Sync-context enqueue. Failures are logged but don't propagate —
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
            logger.error("failed to enqueue %s(%r,%r): %s", name, args, kwargs, e)
        return

    task = loop.create_task(aenqueue(name, *args, **kwargs))
    task.add_done_callback(_log_task_failure(name, args, kwargs))
