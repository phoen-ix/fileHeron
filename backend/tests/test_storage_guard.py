"""Low-disk degradation: disk stats, the critical-low flag gate, and the
hourly disk_check cron that maintains the flag + alerts admins."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import storage as storage_svc


def test_get_disk_stats_fails_open_on_bad_path():
    stats = storage_svc.get_disk_stats("/nonexistent/path/xyz")
    assert "error" in stats
    assert stats["free_bytes"] == 0


def test_is_storage_critical_low_by_percent(db, monkeypatch):
    monkeypatch.setattr(
        storage_svc, "get_disk_stats",
        lambda _p: {"total_bytes": 100, "free_bytes": 90, "used_bytes": 10, "percent_free": 2.0},
    )
    # 2% free < 5% default threshold → critical.
    assert storage_svc.is_storage_critical_low(db, "/data/files") is True


def test_is_storage_critical_low_by_bytes(db, monkeypatch):
    monkeypatch.setattr(
        storage_svc, "get_disk_stats",
        lambda _p: {"total_bytes": 10**15, "free_bytes": 1024, "used_bytes": 0, "percent_free": 80.0},
    )
    # 80% free but only 1 KiB left < 10 GiB default → critical.
    assert storage_svc.is_storage_critical_low(db, "/data/files") is True


def test_is_storage_critical_low_healthy(db, monkeypatch):
    monkeypatch.setattr(
        storage_svc, "get_disk_stats",
        lambda _p: {"total_bytes": 10**15, "free_bytes": 5 * 10**14, "used_bytes": 0, "percent_free": 50.0},
    )
    assert storage_svc.is_storage_critical_low(db, "/data/files") is False


def test_is_storage_critical_low_unknown_fails_open(db, monkeypatch):
    monkeypatch.setattr(storage_svc, "get_disk_stats", lambda _p: {"error": "boom"})
    assert storage_svc.is_storage_critical_low(db, "/data/files") is False


def test_refuse_if_storage_critical_gate(db):
    from app.routers.uploads import _refuse_if_storage_critical

    # Flag unset → no raise.
    _refuse_if_storage_critical(db)

    settings_svc.set_value(
        db, key=settings_svc.Keys.STORAGE_CRITICAL_LOW, value="true", actor=None
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        _refuse_if_storage_critical(db)
    assert exc.value.status_code == 507
    assert exc.value.code == "STORAGE_CRITICAL_LOW"


@pytest.mark.asyncio
async def test_disk_check_flips_flag_and_alerts(db, make_user, monkeypatch):
    from app.models.notification import Notification
    from app.workers import disk_check as disk_check_mod

    admin = make_user(email="admin@test.local", role=UserRole.admin)

    monkeypatch.setattr(
        storage_svc, "get_disk_stats",
        lambda _p: {"total_bytes": 100, "free_bytes": 1, "used_bytes": 99, "percent_free": 1.0},
    )
    # Make the dedup check fail-open fast (no Redis in the sandbox).
    monkeypatch.setattr(disk_check_mod, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError()))

    result = await disk_check_mod.disk_check(None)
    assert result["is_critical"] is True
    assert result["transitioned"] is True

    # Flag flipped on.
    assert settings_svc.get_bool(db, settings_svc.Keys.STORAGE_CRITICAL_LOW) is True
    # Admin got an in-app ops_alert.
    n = db.query(Notification).filter(Notification.user_id == admin.id).count()
    assert n >= 1


@pytest.mark.asyncio
async def test_disk_check_recovers_flag(db, make_user, monkeypatch):
    from app.workers import disk_check as disk_check_mod

    make_user(email="admin@test.local", role=UserRole.admin)
    # Pre-set the flag as if we were previously critical.
    settings_svc.set_value(
        db, key=settings_svc.Keys.STORAGE_CRITICAL_LOW, value="true", actor=None
    )
    db.commit()

    monkeypatch.setattr(
        storage_svc, "get_disk_stats",
        lambda _p: {"total_bytes": 10**15, "free_bytes": 5 * 10**14, "used_bytes": 0, "percent_free": 50.0},
    )
    monkeypatch.setattr(disk_check_mod, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError()))

    result = await disk_check_mod.disk_check(None)
    assert result["is_critical"] is False
    assert result["transitioned"] is True
    assert settings_svc.get_bool(db, settings_svc.Keys.STORAGE_CRITICAL_LOW) is False
