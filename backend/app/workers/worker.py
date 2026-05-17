"""ARQ worker entry point.

Run with: ``arq app.workers.worker.WorkerSettings``

Cron jobs (configured here, not via a separate arq.scheduler package):
- expire_files: hourly at minute=0
- share_expiring_24h_warning: hourly at minute=7
- cleanup_expired_tokens: hourly at minute=23
"""
from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from ..config import settings
from ..utils.logger import configure_logging
from ..services.release_check import release_check
from .av_scan import av_scan_file
from .cleanup_abandoned_uploads import cleanup_abandoned_uploads
from .cleanup_expired_tokens import cleanup_expired_tokens
from .cleanup_pending_invites import cleanup_pending_invites
from .expire_files import expire_files
from .ops_check import ops_check
from .prune_history import prune_history
from .purge_old_quarantine import purge_old_quarantine
from .quota_reconcile import quota_reconcile
from .send_email import send_email_job
from .share_expiring import share_expiring_24h_warning


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
        cleanup_abandoned_uploads,
        purge_old_quarantine,
        prune_history,
        release_check,
    ]
    cron_jobs = [
        # Stagger so they don't pile up at minute 0. ops_check sits at :15
        # so it sees the most recent expire_files (:00) + share_expiring
        # (:07) outcomes when it scans for failures.
        cron(expire_files, hour=None, minute={0}, run_at_startup=False),
        cron(share_expiring_24h_warning, hour=None, minute={7}, run_at_startup=False),
        cron(ops_check, hour=None, minute={15}, run_at_startup=False),
        cron(cleanup_expired_tokens, hour=None, minute={23}, run_at_startup=False),
        cron(quota_reconcile, hour=None, minute={37}, run_at_startup=False),
        # Hourly TUS orphan sweep.
        cron(cleanup_abandoned_uploads, hour=None, minute={47}, run_at_startup=False),
        # GitHub releases poll for in-app "update available" surface.
        cron(release_check, hour=None, minute={53}, run_at_startup=False),
        # Daily-ish housekeeping (hour=2 keeps it well clear of business hours).
        cron(purge_old_quarantine, hour={2}, minute={13}, run_at_startup=False),
        cron(cleanup_pending_invites, hour={2}, minute={15}, run_at_startup=False),
        cron(prune_history, hour={2}, minute={43}, run_at_startup=False),
    ]
    on_startup = startup
    # Use a dedicated queue so the worker doesn't accidentally pick up
    # ad-hoc job enqueues from elsewhere if those ever appear.
    queue_name = "fileheron:default"
    # Retry transient AVUnavailable errors a few times before giving up.
    # Backoff defaults are roughly: 0s, 5s, 25s, 60s … (ARQ's exponential).
    max_tries = 5
