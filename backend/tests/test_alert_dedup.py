"""services/alert_dedup.py - one alert per key per window, with or without Redis.

The four scheduled checks each answered "already alerted?" with their own
exists-then-set and returned "no" whenever Redis raised, so a Redis outage
re-sent every alert on every run, `redis_unhealthy` included. These pin the
atomic SET NX EX, the in-process record that answers during an outage, and the
outage behaviour through the real callers.
"""
from __future__ import annotations

import pytest

from app.models.notification import Notification
from app.models.user import UserRole
from app.services import alert_dedup


def _redis_down(monkeypatch):
    def _raise():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(alert_dedup, "get_redis", _raise)


class _Recorder:
    def __init__(self):
        self.calls: list[tuple] = []
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None, nx=False):
        self.calls.append(("set", key, ex, nx))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


def test_redis_path_is_one_atomic_set_nx_ex(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(alert_dedup, "get_redis", lambda: r)
    assert alert_dedup.seen_recently("k", 3600) is False
    assert alert_dedup.seen_recently("k", 3600) is True
    assert r.calls == [("set", "k", 3600, True), ("set", "k", 3600, True)]


def test_an_outage_alerts_once_per_window_not_once_per_run(monkeypatch):
    _redis_down(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(alert_dedup.time, "monotonic", lambda: clock[0])
    assert alert_dedup.seen_recently("k", 3600) is False
    assert alert_dedup.seen_recently("k", 3600) is True
    clock[0] += 3599
    assert alert_dedup.seen_recently("k", 3600) is True
    clock[0] += 2
    assert alert_dedup.seen_recently("k", 3600) is False, "the window must end"


def test_keys_are_independent(monkeypatch):
    _redis_down(monkeypatch)
    assert alert_dedup.seen_recently("a", 3600) is False
    assert alert_dedup.seen_recently("b", 3600) is False


def test_forget_clears_both_records(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(alert_dedup, "get_redis", lambda: r)
    assert alert_dedup.seen_recently("k", 3600) is False
    alert_dedup.forget("k")
    assert alert_dedup.seen_recently("k", 3600) is False

    _redis_down(monkeypatch)
    assert alert_dedup.seen_recently("j", 3600) is False
    alert_dedup.forget("j")
    assert alert_dedup.seen_recently("j", 3600) is False


def test_ops_check_sends_redis_unhealthy_once_during_the_outage(db, make_user, monkeypatch):
    """The alert that says Redis is down must still go out - once, not hourly."""
    from app.workers import ops_check

    _redis_down(monkeypatch)
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sent = [ops_check._alert_admins(db, reason="redis_unhealthy", detail="probe") for _ in range(3)]
    assert sent == [1, 0, 0]
    db.commit()
    assert db.query(Notification).filter(Notification.user_id == admin.id).count() == 1


@pytest.mark.asyncio
async def test_anomaly_check_does_not_repeat_during_an_outage(db, make_user, monkeypatch):
    """Two runs, one finding, Redis down: one alert and one audit row, where the
    old fallback produced one of each per run."""
    from app.models.audit_log import AuditEventType, AuditLog
    from app.models.download_log import DownloadLog, DownloadVia
    from app.services import settings as ssvc
    from app.utils.timeutil import utc_now
    from app.workers import anomaly_check as ac
    from tests.test_anomaly_worker import _real_file

    _redis_down(monkeypatch)
    make_user(email="admin@test.local", role=UserRole.admin)
    user = make_user(email="u@test.local", role=UserRole.client)
    ssvc.set_value(db, key=ssvc.Keys.ANOMALY_MASS_DOWNLOAD_THRESHOLD, value="3", actor=None)
    file_id, share_id = _real_file(db)
    for _ in range(5):
        db.add(DownloadLog(
            file_id=file_id, share_id=share_id, accessed_by_user_id=user.id, ip="1.1.1.1",
            via=DownloadVia.auth, accessed_at=utc_now(),
        ))
    db.commit()

    first = await ac.anomaly_check(None)
    second = await ac.anomaly_check(None)
    assert first["alerted"] >= 1
    assert second["alerted"] == 0
    db.expire_all()
    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.anomaly_detected.value
    ).count() == first["alerted"]


@pytest.mark.asyncio
async def test_a_failing_cron_alerts_once_during_an_outage(db, make_user, monkeypatch):
    from app.services import cron_tracker

    _redis_down(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(cron_tracker, "dispatch", lambda *_a, **kw: sent.append(kw["category"]))
    make_user(email="admin@test.local", role=UserRole.admin)

    @cron_tracker.track_cron("test_job_outage")
    async def _job(_ctx):
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await _job({})
    assert len(sent) == 1
