"""The scheduled-task status pages cost a fixed number of queries.

/admin/system/status ran a latest-run query and a 24h-count query per task, and
/admin/crons added about seven settings reads per task on top - roughly 200
statements for the 22 tasks in the registry, growing with every task added.
Both now read every task at once (cron_tracker.latest_runs/run_counts_since,
cron_schedule.snapshot). These pin the budget by TABLE, independent of how many
tasks exist, and the values the aggregation must still produce.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.cron_run import CronRun, CronRunStatus
from app.models.user import UserRole
from app.services import cron_schedule as cs
from app.services import cron_tracker
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


async def _admin(make_user, login_as) -> dict:
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return {"Authorization": f"Bearer {token}"}


def _run(db, job, *, at, status=CronRunStatus.success, error=None):
    row = CronRun(job_name=job, started_at=at, status=status, error_msg=error)
    db.add(row)
    db.flush()
    return row


def _seed(db):
    now = utc_now().replace(microsecond=0)
    for name in list(cs.REGISTRY)[:8]:
        for i in range(3):
            _run(db, name, at=now - timedelta(minutes=10 * i))
    db.commit()


def _touching(stmts, table):
    return [s for s in stmts if table in s.lower()]


async def _per_table(client, headers, counting, url) -> dict[str, int]:
    counting.clear()
    r = await client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return {t: len(_touching(counting, t)) for t in ("cron_runs", "app_settings")}


def _two_task_registry(monkeypatch):
    from app.routers.admin import system as system_router

    small = dict(list(cs.REGISTRY.items())[:2])
    monkeypatch.setattr(cs, "REGISTRY", small)
    monkeypatch.setattr(system_router, "_KNOWN_CRONS", list(small))


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/api/admin/system/status", "/api/admin/crons"])
async def test_the_status_pages_cost_the_same_for_two_tasks_or_all_of_them(
    db, make_user, login_as, client, counting, monkeypatch, url
):
    """The invariant is that the cost does not grow with the registry: the same
    statements per table for 22 tasks as for 2."""
    headers = await _admin(make_user, login_as)
    _seed(db)
    full = await _per_table(client, headers, counting, url)
    _two_task_registry(monkeypatch)
    small = await _per_table(client, headers, counting, url)
    assert full == small, f"{url} grows with the registry: {full} vs {small}"
    # newest run per task + grouped 24h counts (+ the recent-failures list on
    # the system page)
    assert full["cron_runs"] <= 3, full


def test_the_newest_run_wins_a_same_second_tie_by_id(db):
    at = utc_now().replace(microsecond=0)
    _run(db, "expire_files", at=at - timedelta(hours=1), status=CronRunStatus.failure)
    first = _run(db, "expire_files", at=at, status=CronRunStatus.failure, error="first")
    second = _run(db, "expire_files", at=at, status=CronRunStatus.success)
    db.commit()
    latest = cron_tracker.latest_runs(db)
    assert latest["expire_files"].id == second.id > first.id


def test_counts_cover_the_window_and_every_status(db):
    now = utc_now()
    _run(db, "imap_poll", at=now - timedelta(minutes=5))
    _run(db, "imap_poll", at=now - timedelta(minutes=6), status=CronRunStatus.failure)
    _run(db, "imap_poll", at=now - timedelta(minutes=7), status=CronRunStatus.running)
    _run(db, "imap_poll", at=now - timedelta(hours=25), status=CronRunStatus.failure)
    _run(db, "expire_files", at=now - timedelta(minutes=1))
    db.commit()
    counts = cron_tracker.run_counts_since(db, now - timedelta(hours=24))
    assert counts["imap_poll"] == {"success": 1, "failure": 1, "running": 1}
    assert counts["expire_files"] == {"success": 1}


@pytest.mark.asyncio
async def test_the_crons_page_shows_stored_settings_and_runs(db, make_user, login_as, client):
    headers = await _admin(make_user, login_as)
    now = utc_now().replace(microsecond=0)
    _run(db, "expire_files", at=now - timedelta(minutes=2), status=CronRunStatus.failure, error="boom")
    for key, value in {
        "cron.expire_files.enabled": "false",
        "cron.expire_files.kind": "daily",
        "cron.expire_files.daily_time": "04:30",
        "cron.expire_files.alert_on_failure": "true",
        "cron.expire_files.last_run_at": (now - timedelta(minutes=2)).isoformat(),
    }.items():
        settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()

    r = await client.get("/api/admin/crons", headers=headers)
    item = next(i for i in r.json()["items"] if i["name"] == "expire_files")
    assert (item["enabled"], item["kind"], item["daily_time"], item["alert_on_failure"]) == (
        False, "daily", "04:30", True,
    )
    assert item["last_status"] == "failure" and item["last_error"] == "boom"
    assert item["last_24h"] == {"success": 0, "failure": 1, "running": 0}
    assert item["next_run_at"] is None  # disabled


def test_the_bulk_snapshot_agrees_with_the_per_task_read(db):
    """cron_dispatch still resolves one task at a time; the pages use the
    snapshot. The two must never disagree, junk values included."""
    for key, value in {
        "cron.imap_poll.enabled": "off",
        "cron.imap_poll.interval_minutes": "not-a-number",
        "cron.expire_files.kind": "weekly",
        "cron.expire_files.interval_minutes": "1",
        "cron.prune_history.alert_on_failure": "yes",
        settings_svc.Keys.ERROR_ALERT_SOURCE_WORKER: "false",
    }.items():
        settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()
    snap = cs.snapshot(db)
    for name in cs.REGISTRY:
        assert snap.schedules[name] == cs.effective(db, name), name
        assert snap.last_runs[name] == cs.get_last_run(db, name), name
