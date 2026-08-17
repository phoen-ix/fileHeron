"""ARQ worker entry point.

Run with: ``arq app.workers.worker.WorkerSettings``

Cron jobs are NOT scheduled here. Since v1.28.0 `cron_jobs` holds a single
minute-ticking dispatcher and every job's cadence, enable flag and kind live in
`services/cron_schedule.py::REGISTRY` (admin-editable at
`/admin/scheduled-tasks`). This docstring used to carry a static table of
per-job minute assignments - `release_check: minute=53` and fifteen more - none
of which had governed anything for five releases; read `REGISTRY` for the real
defaults.

`functions` below is still the authoritative list of what the worker can run:
the dispatcher enqueues from it, "Run now" enqueues from it, and the
event-driven jobs (av_scan_file, send_email_job, webhook_deliver, …) are only
ever enqueued directly.
"""
from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from ..config import settings
from ..services.release_check import release_check
from ..utils.logger import configure_logging
from .analytics_aggregate import analytics_aggregate
from .announce_ready_shares import announce_ready_shares
from .anomaly_check import anomaly_check
from .av_scan import av_scan_file
from .cleanup_abandoned_uploads import cleanup_abandoned_uploads
from .cleanup_expired_tokens import cleanup_expired_tokens
from .cleanup_pending_invites import cleanup_pending_invites
from .cleanup_read_notifications import cleanup_read_notifications
from .cleanup_stale_uploads import cleanup_stale_uploads
from .cron_dispatch import cron_dispatch
from .disk_check import disk_check
from .drain_pending_update import drain_pending_update
from .expire_files import expire_files
from .imap_poll import imap_poll
from .notify_admin_error import notify_admin_error
from .ops_check import ops_check
from .prune_history import prune_history
from .purge_old_quarantine import purge_old_quarantine
from .quota_reconcile import quota_reconcile
from .reclaim_orphaned_files import reclaim_orphaned_files
from .rescan_inbound_attachments import rescan_inbound_attachments
from .send_email import send_email_job
from .share_expiring import share_expiring_24h_warning
from .webhook_deliver import webhook_deliver


async def startup(_ctx) -> None:
    configure_logging(settings.LOG_LEVEL)


class WorkerSettings:
    redis_settings = RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    functions = [
        expire_files,
        av_scan_file,
        send_email_job,
        share_expiring_24h_warning,
        cleanup_expired_tokens,
        cleanup_pending_invites,
        quota_reconcile,
        ops_check,
        disk_check,
        cleanup_abandoned_uploads,
        cleanup_stale_uploads,
        purge_old_quarantine,
        prune_history,
        release_check,
        cleanup_read_notifications,
        reclaim_orphaned_files,
        analytics_aggregate,
        webhook_deliver,
        anomaly_check,
        imap_poll,
        rescan_inbound_attachments,
        drain_pending_update,
        announce_ready_shares,
        cron_dispatch,
        notify_admin_error,
    ]
    cron_jobs = [
        # v1.28.0: cadence/enable/disable for every job is admin-editable. A single
        # dispatcher ticks every minute and enqueues jobs whose configured schedule
        # is due (services/cron_schedule.py + workers/cron_dispatch.py). The job
        # functions above stay enqueueable (dispatcher + on-demand "Run now").
        cron(cron_dispatch, hour=None, minute=set(range(60)), run_at_startup=False),
    ]
    on_startup = startup
    # Use a dedicated queue so the worker doesn't accidentally pick up
    # ad-hoc job enqueues from elsewhere if those ever appear.
    queue_name = "fileheron:default"
    # Retry transient AVUnavailableError errors a few times before giving up.
    # Backoff defaults are roughly: 0s, 5s, 25s, 60s … (ARQ's exponential).
    max_tries = 5
    # ARQ's default job_timeout is 300s, and it cancels the task rather than
    # letting it finish. `av_scan.SOCKET_TIMEOUT_SEC` is 1800s, chosen so a slow
    # scan of a large nested archive produces a real verdict - but that ceiling
    # was unreachable: arq killed the job at 300s first, arq treats the
    # CancelledError as a retry so all five tries burned back to back, the file
    # returned to `ready_unscanned`, and the hourly sweep re-enqueued it with
    # job_try reset - the same file failing the same way forever, which is the
    # exact loop SOCKET_TIMEOUT_SEC was raised to close. The socket ceiling has
    # to be the one that fires, so this sits above it with slack for the DB work
    # either side (audit 2026-07-30 residual sweep).
    job_timeout = 2100
