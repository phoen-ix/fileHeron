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
from .av_scan import av_scan_file
from .cleanup_expired_tokens import cleanup_expired_tokens
from .expire_files import expire_files
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
    ]
    cron_jobs = [
        # Stagger so they don't pile up at minute 0.
        cron(expire_files, hour=None, minute={0}, run_at_startup=False),
        cron(share_expiring_24h_warning, hour=None, minute={7}, run_at_startup=False),
        cron(cleanup_expired_tokens, hour=None, minute={23}, run_at_startup=False),
    ]
    on_startup = startup
    # Use a dedicated queue so the worker doesn't accidentally pick up
    # ad-hoc job enqueues from elsewhere if those ever appear.
    queue_name = "fileheron:default"
    # Retry transient AVUnavailable errors a few times before giving up.
    # Backoff defaults are roughly: 0s, 5s, 25s, 60s … (ARQ's exponential).
    max_tries = 5
