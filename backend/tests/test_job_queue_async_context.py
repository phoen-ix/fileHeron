"""Regression: ``enqueue`` must work from inside a running event loop.

Bug: an `async def` route called the sync ``enqueue`` helper, which
ran ``asyncio.run`` and crashed with ``RuntimeError: asyncio.run()
cannot be called from a running event loop``. The ``except`` clause
swallowed the error and only logged it, so uploads succeeded silently
with no AV scan ever queued. ``sss.txt`` (and a smoke file from the
day before) sat at ``state=ready_unscanned`` until the bug was found.

Both ``enqueue`` (loop-aware) and ``aenqueue`` (await-style) must
actually push to Redis.
"""
from __future__ import annotations

import asyncio
import pytest

from app.services import job_queue


class _FakePool:
    def __init__(self, calls: list):
        self._calls = calls

    async def enqueue_job(self, name, *args, **kwargs):
        self._calls.append((name, args, kwargs))

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _restore_real_enqueue(monkeypatch):
    """conftest._no_op_job_queue stubs aenqueue + enqueue to silence
    Redis-unreachable warnings during the rest of the suite. This file
    is the one place we DO want to test the real implementations, so
    re-import the originals from the module dict and re-bind."""
    import importlib

    fresh = importlib.reload(job_queue)
    monkeypatch.setattr(job_queue, "aenqueue", fresh.aenqueue)
    monkeypatch.setattr(job_queue, "enqueue", fresh.enqueue)


@pytest.fixture
def captured(monkeypatch):
    calls: list = []

    async def _fake_create_pool(_settings):
        return _FakePool(calls)

    monkeypatch.setattr(job_queue, "create_pool", _fake_create_pool)
    return calls


@pytest.mark.asyncio
async def test_aenqueue_pushes_to_redis(captured):
    await job_queue.aenqueue("av_scan_file", "file-123")
    assert captured == [
        ("av_scan_file", ("file-123",), {"_queue_name": "fileheron:default"})
    ]


@pytest.mark.asyncio
async def test_enqueue_in_async_context_schedules_via_create_task(captured):
    """The pre-fix code raised RuntimeError here and silently dropped
    the job. After the fix, the call schedules a task on the running
    loop; we yield once so the create_task body runs."""
    job_queue.enqueue("av_scan_file", "file-456")
    # Yield control so the scheduled task can run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert captured == [
        ("av_scan_file", ("file-456",), {"_queue_name": "fileheron:default"})
    ]


def test_enqueue_in_sync_context_runs_immediately(captured):
    """Sync callers (cron, threadpool routes) keep working unchanged."""
    job_queue.enqueue("av_scan_file", "file-sync")
    assert captured == [
        ("av_scan_file", ("file-sync",), {"_queue_name": "fileheron:default"})
    ]
