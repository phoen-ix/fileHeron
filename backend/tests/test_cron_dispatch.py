"""Cron minute-dispatcher (v1.28.0)."""
from __future__ import annotations

import pytest

from app.services import cron_schedule as cs
from app.services import settings as s
from app.utils.timeutil import utc_now
from datetime import timedelta


@pytest.mark.asyncio
async def test_dispatch_enqueues_due_seeds_unseen_skips_disabled(db, monkeypatch):
    import app.workers.cron_dispatch as cd

    enqueued: list[str] = []

    async def _fake_aenqueue(name, *a, **k):
        enqueued.append(name)

    monkeypatch.setattr(cd.job_queue, "aenqueue", _fake_aenqueue)

    now = utc_now()
    # expire_files: seeded in the past + due (interval 60).
    s.set_value(db, key="cron.expire_files.last_run_at",
                value=(now - timedelta(hours=2)).isoformat(), actor=None)
    # quota_reconcile: disabled -> never enqueued (even though it'd be due).
    s.set_value(db, key="cron.quota_reconcile.enabled", value="false", actor=None)
    s.set_value(db, key="cron.quota_reconcile.last_run_at",
                value=(now - timedelta(hours=2)).isoformat(), actor=None)
    # ops_check: seeded recently -> not due yet.
    s.set_value(db, key="cron.ops_check.last_run_at",
                value=(now - timedelta(minutes=5)).isoformat(), actor=None)
    db.commit()

    result = await cd.cron_dispatch(None)

    assert "expire_files" in enqueued
    assert "quota_reconcile" not in enqueued  # disabled
    assert "ops_check" not in enqueued  # not due
    # Every other (unseeded) cron was seeded, not enqueued.
    assert result["enqueued"] == enqueued
    # expire_files' clock advanced so it won't double-fire next tick.
    last = cs.get_last_run(db, "expire_files")
    assert last is not None and (now - last) < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_dispatch_seeds_unseen_without_enqueue(db, monkeypatch):
    import app.workers.cron_dispatch as cd

    enqueued: list[str] = []

    async def _fake_aenqueue(name, *a, **k):
        enqueued.append(name)

    monkeypatch.setattr(cd.job_queue, "aenqueue", _fake_aenqueue)
    # Fresh DB: nothing seeded -> first tick seeds everything, enqueues nothing.
    await cd.cron_dispatch(None)
    assert enqueued == []
    assert cs.get_last_run(db, "prune_history") is not None
