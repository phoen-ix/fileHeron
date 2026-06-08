"""ARQ worker entry point.

Run with: ``arq app.workers.worker.WorkerSettings``

Cron jobs (configured here, not via a separate arq.scheduler package).
Hourly, staggered so they don't pile up at minute 0:
- expire_files: minute=0
- share_expiring_24h_warning: minute=7
- ops_check: minute=15            (sees the :00/:07 outcomes when scanning for failures)
- disk_check: minute=19           (low-disk guard → storage.critical_low + admin alert)
- cleanup_expired_tokens: minute=23
- anomaly_check: minute=33        (heuristic mass-download / multi-network / stuffing scan)
- quota_reconcile: minute=37
- cleanup_stale_uploads: minute=41
- cleanup_abandoned_uploads: minute=47
- release_check: minute=53
Daily housekeeping at 02:xx (well clear of business hours):
- analytics_aggregate: 02:05  (storage snapshot for the admin analytics trend)
- purge_old_quarantine: 02:13
- cleanup_pending_invites: 02:15
- cleanup_read_notifications: 02:29
- prune_history: 02:43
- reclaim_orphaned_files: 02:51
Plus event-driven jobs (not cron): av_scan_file, send_email_job.
"""
from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from ..config import settings
from ..services.release_check import release_check
from ..utils.logger import configure_logging
from .analytics_aggregate import analytics_aggregate
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
