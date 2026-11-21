"""Cron minute-dispatcher (v1.28.0)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import cron_schedule as cs
from app.services import settings as s
from app.utils.timeutil import utc_now


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


@pytest.mark.asyncio
async def test_a_tick_reads_the_schedules_in_a_constant_number_of_queries(db, monkeypatch, counting):
    """`effective` + `get_last_run` per registry entry was three SELECTs a job,
    ~70 a minute for 23 jobs, every minute, forever. One `snapshot` per tick
    answers all of them in two - the same read the Scheduled-tasks page makes
    (audit 2026-11-08). Seed every job first so the second tick has nothing to
    write, then count: the same statements for the whole registry as for two."""
    from app.workers import cron_dispatch as cd

    async def _fake_aenqueue(_name, **_kw):
        return None

    monkeypatch.setattr(cd.job_queue, "aenqueue", _fake_aenqueue)

    async def _tick_selects() -> int:
        await cd.cron_dispatch(None)  # seeds last_run_at for every job it has not seen
        counting.clear()
        await cd.cron_dispatch(None)
        return len([q for q in counting if q.lstrip().upper().startswith("SELECT") and "app_settings" in q.lower()])

    full = await _tick_selects()
    small = dict(list(cs.REGISTRY.items())[:2])
    monkeypatch.setattr(cs, "REGISTRY", small)
    two = await _tick_selects()

    assert full == two, f"the dispatcher's settings reads grow with the registry: {full} vs {two}"
    assert full <= 3, full  # the snapshot's prefix read + its source-worker default + the site timezone


@pytest.mark.asyncio
async def test_a_task_that_cannot_be_dispatched_alarms_instead_of_only_logging(
    db, make_user, monkeypatch
):
    """The dispatcher is not a tracked cron, so its own failure was a log line:
    a task whose dispatch raised tick after tick silently stopped running
    (found by tests/test_worker_errors_are_reported.py, 2026-11-21)."""
    import app.workers.cron_dispatch as cd
    from app.models.notification import Notification
    from app.models.user import UserRole

    admin_id = make_user(email="admin@test.local", role=UserRole.admin).id
    enqueued: list[str] = []

    async def _aenqueue(name, *a, **k):
        if name == "expire_files":
            raise ConnectionError("queue unreachable")
        enqueued.append(name)

    monkeypatch.setattr(cd.job_queue, "aenqueue", _aenqueue)
    past = (utc_now() - timedelta(hours=2)).isoformat()
    for job in ("expire_files", "quota_reconcile"):
        s.set_value(db, key=f"cron.{job}.last_run_at", value=past, actor=None)
    db.commit()

    await cd.cron_dispatch(None)
    await cd.cron_dispatch(None)  # the next tick fails the same way

    # The other task still ran, and the admin was told once, with the task named.
    assert enqueued.count("quota_reconcile") == 1
    alerts = db.query(Notification).filter(Notification.user_id == admin_id).all()
    assert len(alerts) == 1
    assert alerts[0].payload_json["reason"] == "cron_failing"
    assert "expire_files" in alerts[0].payload_json["detail"]
    assert "queue unreachable" in alerts[0].payload_json["detail"]
