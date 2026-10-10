"""Cron schedule registry + due-logic (v1.28.0)."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services import cron_schedule as cs


def _res(**kw):
    base = {
        "name": "x", "group": "g", "description": "d", "enabled": True,
        "kind": "interval", "interval_minutes": 60, "daily_time": "02:00",
    }
    base.update(kw)
    return cs.ResolvedSchedule(**base)


def test_registry_covers_all_jobs():
    """Counted against the WORKER's cron list rather than a literal: the number
    is not the property - "every scheduled job is in the registry, and the
    registry schedules nothing that does not exist" is."""
    from app.workers import worker

    # `functions`, not `cron_jobs`: the single minute dispatcher enqueues by
    # NAME from the registry (v1.28.0), so a registry entry with no matching
    # worker function is a job that can never run.
    # An entry is a coroutine, or arq's `func(...)` wrapper (per-job limits)
    # carrying its name in `.name`.
    scheduled = {getattr(fn, "name", None) or fn.__name__ for fn in worker.WorkerSettings.functions}
    assert len(cs.REGISTRY) >= 20
    assert "imap_poll" in cs.REGISTRY and "prune_history" in cs.REGISTRY
    assert "drain_pending_update" in cs.REGISTRY
    assert "rescan_inbound_attachments" in cs.REGISTRY  # audit L18
    # release_check defaults to ~daily so it doesn't poll GitHub hourly.
    assert cs.REGISTRY["release_check"].default_interval_min == 1440
    assert "announce_ready_shares" in cs.REGISTRY
    if scheduled:
        missing = {n for n in cs.REGISTRY if n not in scheduled and n != "cron_dispatch"}
        assert missing == set(), f"in the registry but not scheduled: {missing}"


def test_interval_due():
    now = datetime(2026, 6, 7, 12, 0, 0)
    r = _res(kind="interval", interval_minutes=60)
    assert cs.is_due(r, now - timedelta(minutes=61), now, "UTC") is True
    assert cs.is_due(r, now - timedelta(minutes=30), now, "UTC") is False


def test_disabled_never_due():
    now = datetime(2026, 6, 7, 12, 0, 0)
    r = _res(enabled=False)
    assert cs.is_due(r, now - timedelta(days=2), now, "UTC") is False


def test_unseeded_never_due():
    now = datetime(2026, 6, 7, 12, 0, 0)
    assert cs.is_due(_res(), None, now, "UTC") is False


def test_daily_due_after_time_in_site_tz():
    r = _res(kind="daily", daily_time="02:00")
    # 08:00 UTC == 10:00 Europe/Vienna (summer): past 02:00, last ran yesterday.
    now = datetime(2026, 6, 7, 8, 0, 0)
    assert cs.is_due(r, datetime(2026, 6, 6, 5, 0, 0), now, "Europe/Vienna") is True
    # Already ran today (03:00 Vienna == 01:00 UTC today, after 02:00 sched).
    assert cs.is_due(r, datetime(2026, 6, 7, 1, 0, 0), now, "Europe/Vienna") is False


def test_daily_not_due_before_time():
    r = _res(kind="daily", daily_time="02:00")
    # 23:00 UTC on the 6th == 01:00 Vienna on the 7th: before 02:00 -> not due.
    now = datetime(2026, 6, 6, 23, 0, 0)
    assert cs.is_due(r, datetime(2026, 6, 5, 5, 0, 0), now, "Europe/Vienna") is False


def test_effective_reads_overrides(db):
    from app.services import settings as s
    s.set_value(db, key="cron.expire_files.interval_minutes", value="15", actor=None)
    s.set_value(db, key="cron.expire_files.enabled", value="false", actor=None)
    db.commit()
    res = cs.effective(db, "expire_files")
    assert res.interval_minutes == 15 and res.enabled is False
    assert cs.effective_cadence_minutes(db, "expire_files") == 15


def test_effective_defaults_reproduce_current_cadence(db):
    # No overrides -> hourly interval / daily housekeeping as before.
    assert cs.effective(db, "expire_files").kind == "interval"
    assert cs.effective(db, "expire_files").interval_minutes == 60
    assert cs.effective(db, "prune_history").kind == "daily"
    assert cs.effective(db, "prune_history").daily_time == "02:43"


def test_interval_clamped_to_min(db):
    from app.services import settings as s
    s.set_value(db, key="cron.imap_poll.interval_minutes", value="0", actor=None)
    db.commit()
    # imap_poll min is 1.
    assert cs.effective(db, "imap_poll").interval_minutes == 1


def test_a_job_a_hair_short_of_its_interval_is_still_due():
    """`cron_dispatch` ticks once a minute, takes `now` ONCE per tick and stores
    that same value as last_run_at - so the elapsed time it measures next tick is
    the tick SPACING, and whenever that lands marginally under the interval the
    job waits a whole further tick.

    Measured on the reference instance before `_DUE_SLACK` existed: a 1-minute
    job ran every 91.4s (+52%), drain_pending_update every 82.0s, the 5-minute
    IMAP poll every 347.9s (+16%) and hourly jobs every 3641s. Every interval
    job was slower than the cadence its own admin page advertised.
    """
    now = datetime(2026, 6, 6, 12, 0, 0)
    r = _res(kind=cs.KIND_INTERVAL, interval_minutes=1)
    # The realistic case: one tick apart, a few hundred ms short of 60s.
    assert cs.is_due(r, now - timedelta(seconds=59, milliseconds=800), now, "UTC") is True
    # An hourly job one second short of the hour, same shape.
    r60 = _res(kind=cs.KIND_INTERVAL, interval_minutes=60)
    assert cs.is_due(r60, now - timedelta(minutes=59, seconds=59), now, "UTC") is True


def test_the_slack_cannot_halve_the_shortest_cadence():
    """The slack must stay far below the 1-minute floor on interval_minutes.
    Scaling it to a fraction of the dispatcher tick (30s) would let a 1-minute
    job fire at 30s elapsed - steadying the cadence by destroying it."""
    now = datetime(2026, 6, 6, 12, 0, 0)
    r = _res(kind=cs.KIND_INTERVAL, interval_minutes=1)
    assert cs.is_due(r, now - timedelta(seconds=30), now, "UTC") is False
    assert cs._DUE_SLACK.total_seconds() < 15


# --- Daylight saving ---------------------------------------------------------
#
# Six of the seven daily defaults sit between 02:05 and 02:51 - inside the hour
# that Central European clocks skip in March and repeat in October. The slot was
# built with `now_local.replace(hour=..)`, which kept `now`'s fold: on the night
# the clocks go back every 02:xx job ran twice, and inside the spring-forward gap
# the wall-clock check passed while the slot's UTC instant still lay ahead, so a
# 02:51 job was due on every one-minute tick from 01:00 to 01:51 UTC - 52 runs.


def _replay_dispatcher(daily_time, tz, start_utc, *, hours=24, last_run_at=None):
    """Drive is_due exactly as cron_dispatch does: one tick a minute, and a due
    job's last_run_at becomes that tick's `now` (mark_ran before enqueue)."""
    r = _res(kind=cs.KIND_DAILY, daily_time=daily_time)
    last = last_run_at if last_run_at is not None else start_utc - timedelta(minutes=1)
    runs = []
    now = start_utc
    for _ in range(hours * 60):
        if cs.is_due(r, last, now, tz):
            runs.append(now)
            last = now
        now += timedelta(minutes=1)
    return runs


def test_a_daily_job_in_the_repeated_hour_runs_once_when_clocks_go_back():
    # Vienna, 2026-10-25: 03:00 CEST -> 02:00 CET, so 02:13 happens twice.
    runs = _replay_dispatcher("02:13", "Europe/Vienna", datetime(2026, 10, 24, 18, 0))
    # The FIRST 02:13 (CEST, UTC+2) and never the second (CET, 01:13 UTC).
    assert runs == [datetime(2026, 10, 25, 0, 13)]


def test_a_daily_job_in_the_skipped_hour_runs_once_when_clocks_go_forward():
    # Vienna, 2027-03-28: 02:00 CET -> 03:00 CEST, so 02:51 never happens on the
    # wall clock. It runs once, at 03:51 CEST - not on every tick from 03:00.
    runs = _replay_dispatcher("02:51", "Europe/Vienna", datetime(2027, 3, 27, 18, 0))
    assert runs == [datetime(2027, 3, 28, 1, 51)]


def test_new_york_transitions_run_each_daily_job_once():
    # US clocks change at 02:00 local: 01:xx repeats in November, 02:xx is
    # skipped in March.
    fall = _replay_dispatcher("01:30", "America/New_York", datetime(2026, 10, 31, 18, 0))
    assert fall == [datetime(2026, 11, 1, 5, 30)]  # 01:30 EDT, the first one
    spring = _replay_dispatcher("02:30", "America/New_York", datetime(2027, 3, 13, 18, 0))
    assert spring == [datetime(2027, 3, 14, 7, 30)]  # moved to 03:30 EDT


def test_every_default_daily_job_runs_once_on_both_transition_nights():
    daily = {n: s.default_daily_time for n, s in cs.REGISTRY.items() if s.default_kind == cs.KIND_DAILY}
    assert daily, "the registry has no daily jobs - this test would prove nothing"
    for name, hhmm in daily.items():
        for night in (datetime(2026, 10, 24, 18, 0), datetime(2027, 3, 27, 18, 0)):
            runs = _replay_dispatcher(hhmm, "Europe/Vienna", night)
            assert len(runs) == 1, f"{name} at {hhmm} ran {len(runs)} times the night after {night:%Y-%m-%d}"


def test_a_missed_daily_slot_still_runs_once_later_that_day():
    # The worker was down at 02:43 and comes back at 07:00 Vienna: one catch-up run.
    runs = _replay_dispatcher(
        "02:43", "Europe/Vienna", datetime(2026, 10, 12, 5, 0), hours=10,
        last_run_at=datetime(2026, 10, 11, 0, 43),
    )
    assert runs == [datetime(2026, 10, 12, 5, 0)]


def test_next_run_at_on_transition_nights():
    r = _res(kind=cs.KIND_DAILY, daily_time="02:51")
    # 03:30 CEST on the spring-forward day, before the moved slot: still today.
    assert cs.next_run_at(r, None, datetime(2027, 3, 28, 1, 30), "Europe/Vienna") == datetime(2027, 3, 28, 1, 51)
    r = _res(kind=cs.KIND_DAILY, daily_time="02:13")
    # 02:30 CET, the repeated hour, after the run at the first 02:13: tomorrow.
    assert cs.next_run_at(r, None, datetime(2026, 10, 25, 1, 30), "Europe/Vienna") == datetime(2026, 10, 26, 1, 13)
