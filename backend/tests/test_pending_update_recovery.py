"""A failed update hand-off must not destroy the postponed update.

apply_pending_update clears maintenance and the pending record and commits
BEFORE handing the tag to the updater - deliberate, so a container replaced
mid-call does not come back stuck in maintenance. But that clear used to be
unconditional, so if the hand-off then failed the postponement was simply gone:
maintenance lifted, nothing pending, no retry, no error surfaced to the admin.

That was not a rare path. The drain worker runs in the worker container, which
had no /state bind mount, so release_apply's write to /state/current_job.json
failed EVERY time - postponed updates could never fire (audit 2026-07-30). The
mount is fixed in docker-compose.yml; this covers the failure handling.
"""
from __future__ import annotations

import pytest

from app.services import maintenance as maintenance_svc


@pytest.fixture
def postponed(db):
    record = {"target_tag": "v9.9.9", "deadline_iso": "2099-01-01T00:00:00"}
    maintenance_svc.set_enabled(db, True, actor=None, audit=False)
    maintenance_svc.set_pending_update(db, record, actor=None)
    db.commit()
    return record


def test_failed_handoff_restores_maintenance_and_pending(db, postponed, monkeypatch):
    def _boom(**_kwargs):
        raise OSError("Updater state directory is not writable")

    monkeypatch.setattr("app.services.release_apply.apply", _boom)

    with pytest.raises(OSError):
        maintenance_svc.apply_pending_update(db, reason="drain")

    # The postponement must survive so the next drain tick retries.
    assert maintenance_svc.get_pending_update(db) == postponed
    assert maintenance_svc.is_enabled(db) is True


def test_successful_handoff_clears_state(db, postponed, monkeypatch):
    """Control: the happy path must still clear both, or a successful update
    would leave the new container booting into maintenance mode."""
    monkeypatch.setattr(
        "app.services.release_apply.apply",
        lambda **_kwargs: {"job_id": "job-123"},
    )

    result = maintenance_svc.apply_pending_update(db, reason="drain")

    assert result["job_id"] == "job-123"
    assert maintenance_svc.get_pending_update(db) is None
    assert maintenance_svc.is_enabled(db) is False


def test_nothing_pending_is_a_noop(db):
    assert maintenance_svc.apply_pending_update(db, reason="drain") is None
