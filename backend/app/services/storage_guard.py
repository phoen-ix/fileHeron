"""Storage-pressure gate for new uploads.

Extracted from `routers/uploads.py::_refuse_if_storage_critical` so the tusd
pre-create hook can apply the same rule. It could not before: the guard lived
in the HTTP router, so it covered `/api/uploads/init` and `/api/uploads/direct`
but not `handle_pre_create` - which is the path every upload ABOVE 100 MB
takes. The one class of upload that can actually fill a disk was the one class
the disk-full guard did not cover, and the SPA reaches it without ever touching
a guarded route (audit 2026-07-30).

The flag is the fast path - no `statvfs` on the hot path; the `disk_check` cron
keeps it current.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..middleware.errors import AppError


def refuse_if_critical_low(db: Session) -> None:
    """Raise 507 when the disk_check cron has flagged the storage volume
    critically low. Downloads are deliberately unaffected: shedding reads would
    not free a single byte, and it is the one thing still working."""
    from . import settings as settings_svc

    if settings_svc.get_bool(
        db, settings_svc.Keys.STORAGE_CRITICAL_LOW, default=False
    ):
        raise AppError(
            507,
            "STORAGE_CRITICAL_LOW",
            "Server storage is critically low. Uploads are temporarily unavailable.",
        )
