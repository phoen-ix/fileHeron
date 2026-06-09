"""services/error_log.py - the browsable error log + log-vs-alert decoupling."""
from __future__ import annotations

import pytest

from app.models.error_log import ErrorLog
from app.models.user import UserRole
from app.services import error_alert, error_log
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now

K = settings_svc.Keys


def _event(source="http", status=500, code="INTERNAL_ERROR", path="/api/files/1", job=None, ip="203.0.113.7"):
    ev = {
        "source": source,
        "exception_type": "ValueError",
        "message": "boom",
        "method": "GET" if source == "http" else "CRON",
        "path": path,
        "status_code": status,
        "code": code,
        "ip": ip,
        "request_id": "rid",
        "user_id": 7,
        "auth_via": "session",
        "at": utc_now().isoformat(),
    }
    if job:
        ev["job_name"] = job
        ev["path"] = job
    return ev


# --- parse / should_log ----------------------------------------------------


def test_parse_4xx_codes_keeps_only_4xx():
    assert error_log.parse_4xx_codes("429, 409, 99, 500, abc, 404") == {429, 409, 404}
    assert error_log.parse_4xx_codes("") == set()
    assert error_log.parse_4xx_codes(None) == set()


def test_should_log_5xx_default_on(db):
    assert error_log.should_log(db, _event(status=500)) is True


def test_should_log_respects_disable(db):
    settings_svc.set_value(db, key=K.ERROR_LOG_ENABLED, value="false", actor=None)
    db.commit()
    assert error_log.should_log(db, _event(status=500)) is False


def test_should_log_worker_when_enabled(db):
    ev = _event(source="worker", status=500, code="CRON_FAILED", job="expire_files")
    assert error_log.should_log(db, ev) is True


def test_should_not_log_4xx_by_default(db):
    assert error_log.should_log(db, _event(status=429, code="RATE_LIMITED")) is False


def test_should_log_4xx_only_when_captured_and_allowlisted(db):
    settings_svc.set_value(db, key=K.ERROR_LOG_CAPTURE_4XX, value="true", actor=None)
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="429", actor=None)
    db.commit()
    assert error_log.should_log(db, _event(status=429, code="RATE_LIMITED")) is True
    # Not in the allowlist -> not logged.
    assert error_log.should_log(db, _event(status=404, code="NOT_FOUND")) is False
    # Empty allowlist -> capture nothing even with the toggle on.
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="", actor=None)
    db.commit()
    assert error_log.should_log(db, _event(status=429, code="RATE_LIMITED")) is False


# --- record + read ---------------------------------------------------------


def test_record_and_list_filters(db):
    error_log.record(db, _event(status=500, code="INTERNAL_ERROR", path="/a", ip="10.0.0.1"), signature="sig5")
    error_log.record(db, _event(status=429, code="RATE_LIMITED", path="/b", ip="10.0.0.2"), signature="sig4")
    error_log.record(
        db, _event(source="worker", status=500, code="CRON_FAILED", job="expire_files", ip=None), signature="sigw"
    )
    db.commit()

    rows, total = error_log.list_errors(db)
    assert total == 3
    rows, total = error_log.list_errors(db, code="RATE_LIMITED")
    assert total == 1 and rows[0].status_code == 429
    rows, total = error_log.list_errors(db, status_code=500)
    assert total == 2
    rows, total = error_log.list_errors(db, source="worker")
    assert total == 1 and rows[0].job_name == "expire_files"
    # IP is stored and exact-match filterable (scan triage).
    rows, total = error_log.list_errors(db, ip="10.0.0.1")
    assert total == 1 and rows[0].path == "/a"
    assert rows[0].ip == "10.0.0.1"


def test_record_clips_and_defaults(db):
    rid = error_log.record(
        db,
        {"source": "http", "status_code": 500, "code": "X" * 200, "at": utc_now()},
        signature="s",
    )
    db.commit()
    row = db.get(ErrorLog, rid)
    assert len(row.code) <= 64
    assert row.alerted is False


# --- log/alert decoupling --------------------------------------------------


def test_logged_even_when_alerting_disabled(db):
    # ERROR_ALERT_ENABLED defaults off; ERROR_LOG_ENABLED defaults on.
    res = error_alert.handle_error_event(db, _event(status=500))
    assert res["status"] == "disabled"  # no email
    assert res["logged"] is True  # but logged
    assert db.query(ErrorLog).count() == 1
    assert db.query(ErrorLog).first().alerted is False


def test_alerted_flag_set_on_send(db, make_user, monkeypatch):
    make_user(email="a@test.local", role=UserRole.admin)
    settings_svc.set_value(db, key=K.ERROR_ALERT_ENABLED, value="true", actor=None)
    db.commit()

    def _no_redis():
        raise RuntimeError("no redis")

    monkeypatch.setattr(error_alert, "get_redis", _no_redis)  # cooldown fail-open -> send
    monkeypatch.setattr(error_alert, "_send_to_admins", lambda _db, _p: 1)

    res = error_alert.handle_error_event(db, _event(status=500))
    assert res["status"] == "sent"
    row = db.query(ErrorLog).first()
    assert row.alerted is True


# --- cached 4xx gate (middleware optimisation) -----------------------------


def test_capture_4xx_cached_reflects_setting(db):
    error_log._reset_cache()
    assert error_log.capture_4xx_enabled_cached() is False
    settings_svc.set_value(db, key=K.ERROR_LOG_CAPTURE_4XX, value="true", actor=None)
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="429", actor=None)
    db.commit()
    error_log._reset_cache()
    assert error_log.capture_4xx_enabled_cached() is True
    # Capture on but empty allowlist -> still nothing to enqueue.
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="", actor=None)
    db.commit()
    error_log._reset_cache()
    assert error_log.capture_4xx_enabled_cached() is False


# --- retention -------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_drops_aged_rows(db):
    from datetime import timedelta

    from app.workers.prune_history import _prune_table

    fresh = error_log.record(db, _event(status=500), signature="fresh")
    old = error_log.record(db, _event(status=500), signature="old")
    db.commit()
    # Backdate one row well past the 90d window.
    db.get(ErrorLog, old).created_at = utc_now() - timedelta(days=200)
    db.commit()

    pruned = await _prune_table("error_log", 90, ErrorLog.created_at, ErrorLog)
    assert pruned == 1
    remaining = {r.id for r in db.query(ErrorLog).all()}
    assert remaining == {fresh}
