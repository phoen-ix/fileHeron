"""An unreadable storage root must reach an operator.

From audit #2. `get_disk_stats` fails open by design - it returns zeros plus an
`error` key, and `is_storage_critical_low` answers False, so a filesystem hiccup
cannot refuse every upload. That decision is right and is kept. What was wrong
is that nothing ever said so out loud:

- `disk_check` logged one warning to worker stdout and RETURNED, so `@track_cron`
  recorded the run as a SUCCESS. /admin/scheduled-tasks stayed green.
- No admin notification, no error-log row, no webhook.
- Meanwhile the guard the whole subsystem exists for answers "not critical" on
  every check, the volume fills, and finalize starts 500ing with orphaned quota
  reservations - the precise outcome storage.py says it prevents.
- `/api/metrics` published `fileheron_storage_free_bytes 0`, which a scrape
  target cannot tell apart from a real reading.

The trigger is not hypothetical: a missing bind-mount source coming back
root-owned is a documented recurring failure on this deployment, and the worker
runs as UID 1000.
"""
from __future__ import annotations

import pytest

from app.models.notification import Notification
from app.models.user import UserRole


@pytest.fixture
def statvfs_broken(monkeypatch):
    import os

    def _boom(_path):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "statvfs", _boom)


@pytest.mark.asyncio
async def test_the_cron_fails_loudly_instead_of_reporting_success(
    db, make_user, statvfs_broken, monkeypatch
):
    from app.workers import disk_check as dc

    make_user(email="admin@test.local", role=UserRole.admin)
    db.commit()
    monkeypatch.setattr(dc, "_dedup_seen", lambda: False)

    with pytest.raises(RuntimeError) as exc:
        await dc.disk_check(None)
    assert "unreadable" in str(exc.value)


@pytest.mark.asyncio
async def test_an_admin_is_notified(db, make_user, statvfs_broken, monkeypatch):
    from app.workers import disk_check as dc

    admin = make_user(email="admin2@test.local", role=UserRole.admin)
    db.commit()
    monkeypatch.setattr(dc, "_dedup_seen", lambda: False)

    # The cron tracker reaches Redis on a failure; not the subject here.
    from app.services import cron_tracker

    monkeypatch.setattr(cron_tracker, "_maybe_alert_admins", lambda *a, **kw: None)
    with pytest.raises(RuntimeError):
        await dc.disk_check(None)

    rows = db.query(Notification).filter(Notification.user_id == admin.id).all()
    assert rows, "the storage root vanished and nobody was told"
    assert any("storage_unreadable" in str(r.payload_json) for r in rows)


def test_the_upload_gate_still_fails_open(db, statvfs_broken):
    """The deliberate half. A filesystem hiccup must not refuse every upload -
    which is exactly why the failure has to be surfaced some other way."""
    from app.services import storage as storage_svc

    assert storage_svc.is_storage_critical_low(db, "/does/not/matter") is False


def test_metrics_publishes_no_disk_series_it_cannot_measure(db, statvfs_broken):
    """Zeros here would read as a full disk to any scrape target."""
    from app.routers import metrics as metrics_router
    from app.services import storage as storage_svc

    stats = storage_svc.get_disk_stats("/does/not/matter")
    assert stats["free_bytes"] == 0 and "error" in stats, "the control"

    import inspect

    src = inspect.getsource(metrics_router)
    assert '"error" not in disk' in src, (
        "the gauges are emitted from zeroed stats, publishing a measurement "
        "that was never taken"
    )
