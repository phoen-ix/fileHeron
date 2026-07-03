"""Cron entry for the inbound IMAP poll (v1.27.0).

Cadence/enable live in the admin cron scheduler (services/cron_schedule.py)
since v1.28.0; ``run_poll`` only feature-gates on ``imap.enabled``. IMAP I/O is
blocking stdlib ``imaplib`` → run it in a thread so the worker event loop stays
free.
"""
from __future__ import annotations

import asyncio

from ..services import imap_poll as imap_poll_svc
from ..services.cron_tracker import track_cron


@track_cron("imap_poll")
async def imap_poll(_ctx) -> dict:
    return await asyncio.to_thread(imap_poll_svc.run_poll, manual=False)
