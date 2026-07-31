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


def _real_user(db, user_id):
    """Ensure a User row with this id exists.

    The anomaly tests count downloads BY user id and used bare integers with no
    users behind them. `download_log.accessed_by_user_id` carries a real FK, so
    those rows are rejected by MariaDB - they only inserted because the test
    engine had foreign keys off (audit 2026-07-30, tests-17)."""
    from app.models.user import User, UserRole

    if user_id is None:
        return None
    existing = db.get(User, user_id)
    if existing is not None:
        return existing.id
    u = User(
        id=user_id, email=f"anomaly-{user_id}@test.local",
        display_name=f"User {user_id}", role=UserRole.client,
        password_hash="x", email_verified=True,
    )
    db.add(u)
    db.flush()
    return u.id


def _real_file(db):
    """A committed (share, file) pair for the download rows below to point at.

    These helpers used to insert `file_id="f", share_id="s"` - rows referring to
    nothing. That only worked because the test engine had foreign keys OFF; the
    same insert is rejected by MariaDB, so the suite was exercising a shape
    production cannot produce (audit 2026-07-30, tests-17)."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import User, UserRole

    owner = db.query(User).filter(User.role == UserRole.employee).first()
    if owner is None:
        owner = User(
            email="anomaly-owner@test.local", display_name="Owner",
            role=UserRole.employee, password_hash="x", email_verified=True,
        )
        db.add(owner)
        db.flush()
    # Reused across calls within a test: the download rows only need SOME real
    # parent, and creating a fresh one per call would collide on the file id.
    existing = db.query(File).first()
    if existing is not None:
        return existing.id, existing.share_id

    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(
        id="00000000-0000-0000-0000-0000000000an", share_id=sh.id,
        original_filename="a.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=None, state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    return f.id, sh.id


def _seed_mass_download(db, user_id, n=5):
    file_id, share_id = _real_file(db)
    _real_user(db, user_id)
    for _ in range(n):
        db.add(DownloadLog(
            file_id=file_id, share_id=share_id, accessed_by_user_id=user_id, ip="1.1.1.1",
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
