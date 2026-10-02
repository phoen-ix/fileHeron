"""ARQ jobs of encryption at rest (services/encryption_lanes).

`encrypt_new_files` is event-driven: a scan that leaves a verdict for the
release lane enqueues it. `encrypt_existing_files` is the backfill, a cron in
`cron_schedule.REGISTRY` (every 10 minutes by default), also kicked when the
switch turns on.

Both run in a thread - the encryption is blocking file and object-store I/O and
must not freeze the worker's event loop - and a cancel (worker shutdown, job
timeout) is passed into the thread, which stops between chunks and leaves the
file for the next run.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from ..database import SessionLocal
from ..services import encryption_lanes
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.encrypt_at_rest")


async def _in_thread(body: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    cancel = threading.Event()

    def _run() -> dict[str, Any]:
        db = SessionLocal()
        try:
            return body(db, cancel=cancel)
        finally:
            db.close()

    try:
        return await asyncio.to_thread(_run)
    except asyncio.CancelledError:
        cancel.set()
        raise


async def encrypt_new_files(_ctx) -> dict:
    return await _in_thread(encryption_lanes.run_release_lane)


@track_cron("encrypt_existing_files")
async def encrypt_existing_files(_ctx) -> dict:
    return await _in_thread(encryption_lanes.run_backfill)
