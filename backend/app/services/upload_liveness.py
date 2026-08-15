"""One definition of "is this upload still going?".

Two consumers need that answer and they must not disagree:

  * `workers/cleanup_stale_uploads` reaps rows it judges dead - destructively,
    flipping the parent share to `failed`, a state with no un-fail path.
  * `services/transfer_activity.active_uploads` counts rows it judges alive, so
    the maintenance drain does not restart the stack mid-transfer.

Both used `files.created_at`, which is stamped at /api/uploads/init BEFORE the
first byte moves and is never refreshed - so both were really measuring "time
since this upload started". The sweeper therefore killed every transfer slower
than its cutoff, and the drain went blind to exactly the same transfers.

They also read the cutoff from different places: the sweeper through
`settings_registry.effective` (admin-tunable, live) and the drain straight off
`config.settings` (env only). An admin raising the knob to work around the
reaping moved one and not the other, silently. Both now come through here.

Liveness is `last_progress_at`, stamped by tusd's post-receive hook, which is
the only hook that fires while bytes are arriving. Readers COALESCE to
`created_at` so a row with no progress yet - a direct upload, or anything
written before the column existed - behaves exactly as it did before.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..models.file import File
from ..utils.timeutil import utc_now


def stale_after_hours(db: Session) -> int:
    """The admin-tunable staleness window, in hours."""
    from . import settings_registry

    return max(1, int(settings_registry.effective(db, settings_registry.K.UPLOAD_STALE_AFTER_HOURS)))


def stale_cutoff(db: Session) -> datetime:
    """Rows whose last activity predates this are no longer making progress."""
    return utc_now() - timedelta(hours=stale_after_hours(db))


def last_activity() -> ColumnElement[datetime]:
    """SQL for "when did this row last show signs of life".

    Deliberately NOT filtered on `tus_upload_id IS NOT NULL`: `create_pending`
    sets state=uploading before tusd assigns an id, and a direct upload never
    gets one at all, so that filter would make every direct upload look dead to
    the sweeper and invisible to the drain.
    """
    return func.coalesce(File.last_progress_at, File.created_at)
