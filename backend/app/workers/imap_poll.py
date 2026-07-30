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


class InboundPollError(RuntimeError):
    """Raised so @track_cron records a failed poll as a FAILURE."""


@track_cron("imap_poll")
async def imap_poll(_ctx) -> dict:
    result = await asyncio.to_thread(imap_poll_svc.run_poll, manual=False)
    # run_poll never raises - it catches everything and reports {"ok": False}.
    # @track_cron only marks a run failed when the job raises, so a mailbox that
    # had been unreachable (or misconfigured) for weeks was recorded as a clean
    # SUCCESS every five minutes: no cron_failed audit, no ops_alert, no error
    # log entry, and a green Scheduled Tasks page (audit 2026-07-30). Re-raise so
    # a broken poll is actually visible.
    #
    # "not configured" is excluded on purpose - that is a deployment without
    # inbound mail, not a fault, and alerting on it would train operators to
    # ignore this job.
    if not result.get("ok") and result.get("error") != "not_configured":
        raise InboundPollError(str(result.get("error") or "imap poll failed"))
    return result
