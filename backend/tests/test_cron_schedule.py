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
    assert len(cs.REGISTRY) == 19
    assert "imap_poll" in cs.REGISTRY and "prune_history" in cs.REGISTRY
    assert "drain_pending_update" in cs.REGISTRY
    assert "rescan_inbound_attachments" in cs.REGISTRY  # audit L18
    # release_check defaults to ~daily so it doesn't poll GitHub hourly.
    assert cs.REGISTRY["release_check"].default_interval_min == 1440


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
