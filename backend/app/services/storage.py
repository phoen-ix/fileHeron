"""Disk-space monitoring + low-disk degradation.

A full storage volume used to surface as a silent 500 from the finalize path
that ALSO left the user's quota reservation orphaned. This module turns that
into an explicit, observable signal:

- `get_disk_stats(path)` — `os.statvfs` snapshot (fail-open: returns zeros +
  an `error` key rather than raising, so a transient/unmounted-path hiccup
  never blocks the whole upload pipeline).
- `is_storage_critical_low(db, root)` — true when free space breaches EITHER
  the percent or the bytes threshold (both live-tunable via the settings
  registry). The hourly `workers/disk_check.py` cron consults this, flips the
  `storage.critical_low` flag, and alerts admins; `routers/uploads.py` gates
  new uploads on the same check (downloads stay up).
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from . import settings as settings_svc
from . import settings_registry

logger = logging.getLogger("fileheron.storage")


def get_disk_stats(path: str) -> dict:
    """Return {total_bytes, free_bytes, used_bytes, percent_free}. On error,
    returns zeros plus an `error` key — callers must treat that as 'unknown',
    not 'full' (fail-open)."""
    try:
        st = os.statvfs(path)
        block = st.f_frsize
        total = st.f_blocks * block
        free = st.f_bavail * block
        used = max(0, (st.f_blocks - st.f_bfree) * block)
        percent_free = (free / total * 100.0) if total > 0 else 0.0
        return {
            "total_bytes": max(0, total),
            "free_bytes": max(0, free),
            "used_bytes": used,
            "percent_free": percent_free,
        }
    except Exception as e:
        logger.warning("get_disk_stats failed for path=%s: %s", path, e)
        return {
            "total_bytes": 0,
            "free_bytes": 0,
            "used_bytes": 0,
            "percent_free": 0.0,
            "error": str(e),
        }


def is_storage_critical_low(db: Session, storage_root: str) -> bool:
    """True when free space on `storage_root` is below either threshold.
    Fail-open: an unreadable path (error) is treated as NOT critical so a
    filesystem hiccup can't block every upload."""
    stats = get_disk_stats(storage_root)
    if "error" in stats:
        return False

    threshold_pct = settings_registry.effective(
        db, settings_svc.Keys.STORAGE_LOW_THRESHOLD_PERCENT
    )
    threshold_bytes = settings_registry.effective(
        db, settings_svc.Keys.STORAGE_LOW_THRESHOLD_BYTES
    )
    return stats["percent_free"] < threshold_pct or stats["free_bytes"] < threshold_bytes
