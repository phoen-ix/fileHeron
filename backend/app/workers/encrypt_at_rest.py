"""ARQ jobs of encryption at rest (services/encryption_lanes).

`encrypt_new_files` is event-driven: a scan that leaves a verdict for the
release lane enqueues it. It runs in a thread - the encryption is blocking file
and object-store I/O and must not freeze the worker's event loop - and a cancel
(worker shutdown, job timeout) is passed into the thread, which stops between
chunks and leaves the file's verdict for the next run.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from ..database import SessionLocal
from ..services import encryption_lanes

logger = logging.getLogger("fileheron.workers.encrypt_at_rest")


async def encrypt_new_files(_ctx) -> dict:
    cancel = threading.Event()

    def _run() -> dict:
        db = SessionLocal()
        try:
            return encryption_lanes.run_release_lane(db, cancel=cancel)
        finally:
            db.close()

    try:
        return await asyncio.to_thread(_run)
    except asyncio.CancelledError:
        cancel.set()
        raise
