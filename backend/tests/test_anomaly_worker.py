"""anomaly_check cron - flags, alerts, dedups, honours the kill switch."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.download_log import DownloadLog, DownloadVia
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import settings as ssvc
from app.utils.timeutil import utc_now


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def exists(self, k):
        return k in self.store

    def set(self, k, v, ex=None):
        self.store[k] = v


def _seed_mass_download(db, user_id, n=5):
    for _ in range(n):
        db.add(DownloadLog(
            file_id="f", share_id="s", accessed_by_user_id=user_id, ip="1.1.1.1",
            via=DownloadVia.auth, accessed_at=utc_now(),
        ))


@pytest.mark.asyncio
async def test_anomaly_check_flags_and_alerts(db, make_user, monkeypatch):
    from app.workers import anomaly_check as ac

    monkeypatch.setattr(ac, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError()))
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    user = make_user(email="u@test.local", role=UserRole.client)
    ssvc.set_value(db, key=ssvc.Keys.ANOMALY_MASS_DOWNLOAD_THRESHOLD, value="3", actor=None)
    _seed_mass_download(db, user.id, n=5)
    db.commit()

    res = await ac.anomaly_check(None)
    assert res["alerted"] >= 1

    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.anomaly_detected.value
    ).count() >= 1
    assert db.query(Notification).filter(Notification.user_id == admin.id).count() >= 1


@pytest.mark.asyncio
async def test_anomaly_check_dedups_within_window(db, make_user, monkeypatch):
    from app.workers import anomaly_check as ac

    fake = _FakeRedis()
    monkeypatch.setattr(ac, "get_redis", lambda: fake)
    make_user(email="admin@test.local", role=UserRole.admin)
    user = make_user(email="u@test.local", role=UserRole.client)
    ssvc.set_value(db, key=ssvc.Keys.ANOMALY_MASS_DOWNLOAD_THRESHOLD, value="3", actor=None)
    _seed_mass_download(db, user.id, n=5)
    db.commit()

    r1 = await ac.anomaly_check(None)
    assert r1["alerted"] >= 1
    r2 = await ac.anomaly_check(None)
    assert r2["alerted"] == 0  # same finding, deduped


@pytest.mark.asyncio
async def test_anomaly_check_disabled_is_noop(db, make_user):
    from app.workers import anomaly_check as ac

    user = make_user(email="u@test.local", role=UserRole.client)
    ssvc.set_value(db, key=ssvc.Keys.ANOMALY_ENABLED, value="false", actor=None)
    _seed_mass_download(db, user.id, n=200)
    db.commit()
    res = await ac.anomaly_check(None)
    assert res == {"enabled": False}
