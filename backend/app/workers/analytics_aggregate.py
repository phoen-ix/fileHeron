"""Nightly storage snapshot for the admin analytics dashboard.

Writes one org-level row/day (storage bytes + file-state counts) so the
storage-growth trend has history that deletes can't erase. Every other
analytic is computed live in the endpoint — this cron only feeds the trend.
Idempotent: re-running overwrites today's row.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..services import analytics as analytics_svc
from ..services.cron_tracker import track_cron

logger = logging.getLogger("fileheron.workers.analytics_aggregate")


@track_cron("analytics_aggregate")
async def analytics_aggregate(_ctx) -> dict:
    db = SessionLocal()
    try:
        row = analytics_svc.snapshot_storage_today(db)
        db.commit()
        return {
            "snapshot_date": row.snapshot_date.isoformat(),
            "storage_bytes": row.storage_bytes,
            "files_total": row.files_total,
        }
    finally:
        db.close()
