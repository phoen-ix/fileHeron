"""services/error_alert.py - saferails + send for the email-on-server-error
feature. Covers signature dedup, cooldown, hourly cap, source/master gates,
recipient modes, the 4xx filter in the handler helper, and Redis fail-open."""
from __future__ import annotations

import types

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole
from app.services import error_alert
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now

K = settings_svc.Keys


class _FakeRedis:
    """Minimal in-memory stand-in for the bits error_alert uses."""

    def __init__(self) -> None:
        self.store: dict[str, str | int] = {}

    def incr(self, key: str) -> int:
        v = int(self.store.get(key, 0)) + 1
        self.store[key] = v
        return v

    def expire(self, key: str, _sec: int) -> bool:
        return key in self.store

    def set(self, key: str, value, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key: str):
        v = self.store.get(key)
        return str(v) if v is not None else None

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(error_alert, "get_redis", lambda: r)
    return r


def _enable(db, **overrides):
    settings_svc.set_value(db, key=K.ERROR_ALERT_ENABLED, value="true", actor=None)
    for key, value in overrides.items():
        settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


def _http_event(path="/api/files/123", code="INTERNAL_ERROR", status=500, exc_type="ValueError"):
    return {
        "source": "http",
        "exception_type": exc_type,
        "message": "boom",
        "method": "GET",
        "path": path,
        "status_code": status,
        "code": code,
        "request_id": "rid-1",
        "user_id": None,
        "auth_via": None,
        "at": utc_now().isoformat(),
    }


# --- signature -------------------------------------------------------------


def test_signature_collapses_resource_ids():
    a = error_alert.signature(_http_event(path="/api/files/123"))
    b = error_alert.signature(_http_event(path="/api/files/456"))
    assert a == b  # /api/files/:id


def test_signature_differs_on_code():
    a = error_alert.signature(_http_event(code="INTERNAL_ERROR"))
    b = error_alert.signature(_http_event(code="STORAGE_MISSING"))
    assert a != b


# --- master / source gates -------------------------------------------------


def test_disabled_by_default(db, fake_redis):
    assert error_alert.handle_error_event(db, _http_event())["status"] == "disabled"


def test_http_source_toggle_off(db, fake_redis):
    _enable(db, **{K.ERROR_ALERT_SOURCE_HTTP_5XX: "false"})
    assert error_alert.handle_error_event(db, _http_event())["status"] == "source_disabled"


def test_worker_source_gated_by_per_cron_flag(db, make_user, fake_redis):
    make_user(email="a@test.local", role=UserRole.admin)
    _enable(db)
    ev = {
        "source": "worker", "exception_type": "RuntimeError", "message": "fail",
        "job_name": "expire_files", "path": "expire_files", "method": "CRON",
        "status_code": 500, "code": "CRON_FAILED", "request_id": None,
        "user_id": None, "auth_via": None, "at": utc_now().isoformat(),
    }
    # Per-task flag off -> no email.
    assert error_alert.handle_error_event(db, ev)["status"] == "source_disabled"
    # Flip the per-cron flag on -> sends.
    settings_svc.set_value(db, key="cron.expire_files.alert_on_failure", value="true", actor=None)
    db.commit()
    assert error_alert.handle_error_event(db, ev)["status"] == "sent"


# --- cooldown / dedup ------------------------------------------------------


def test_first_sends_then_deduped(db, make_user, fake_redis):
    make_user(email="a@test.local", role=UserRole.admin)
    _enable(db)
    ev = _http_event()
    assert error_alert.handle_error_event(db, ev)["status"] == "sent"
    assert db.query(Notification).filter(
        Notification.category == NotificationCategory.server_error
    ).count() == 1

    r2 = error_alert.handle_error_event(db, ev)
    assert r2["status"] == "deduped"
    assert r2["occurrences"] == 2
    # No second in-app row (the repeat was suppressed, only counted).
    assert db.query(Notification).filter(
        Notification.category == NotificationCategory.server_error
    ).count() == 1


def test_post_cooldown_resend_reports_suppressed(db, fake_redis, monkeypatch):
    _enable(db)
    captured: dict = {}
    monkeypatch.setattr(
        error_alert, "_send_to_admins",
        lambda _db, payload: captured.update(payload) or 1,
    )
    ev = _http_event()
    assert error_alert.handle_error_event(db, ev)["status"] == "sent"
    assert error_alert.handle_error_event(db, ev)["status"] == "deduped"
    # Simulate the cooldown window elapsing for this signature.
    fake_redis.delete(error_alert._SENT_KEY.format(sig=error_alert.signature(ev)))
    assert error_alert.handle_error_event(db, ev)["status"] == "sent"
    assert captured["occurrence_count"] == 2  # since the last alert
    assert captured["suppressed_count"] == 1
    assert captured["suppressed_since"] is not None


# --- hourly cap ------------------------------------------------------------


def test_hourly_cap_blocks_after_limit(db, fake_redis, monkeypatch):
    _enable(db)
    monkeypatch.setattr(error_alert, "_send_to_admins", lambda _db, _p: 1)
    calls = {"n": 0}

    def fake_cap(_bucket, _ip, limit, window_sec):
        calls["n"] += 1
        return calls["n"] <= 2

    monkeypatch.setattr(error_alert.rate_limit, "check_ip_allowed", fake_cap)
    assert error_alert.handle_error_event(db, _http_event(path="/a"))["status"] == "sent"
    assert error_alert.handle_error_event(db, _http_event(path="/b"))["status"] == "sent"
    assert error_alert.handle_error_event(db, _http_event(path="/c"))["status"] == "rate_capped"


def test_suppressed_events_do_not_burn_cap(db, fake_redis, monkeypatch):
    _enable(db)
    monkeypatch.setattr(error_alert, "_send_to_admins", lambda _db, _p: 1)
    calls = {"n": 0}

    def fake_cap(_bucket, _ip, limit, window_sec):
        calls["n"] += 1
        return True

    monkeypatch.setattr(error_alert.rate_limit, "check_ip_allowed", fake_cap)
    ev = _http_event()
    error_alert.handle_error_event(db, ev)   # sent -> 1 cap check
    error_alert.handle_error_event(db, ev)   # deduped -> no cap check
    assert calls["n"] == 1


# --- recipient modes -------------------------------------------------------


def test_admins_mode_writes_inapp_row_per_admin(db, make_user, fake_redis):
    make_user(email="a1@test.local", role=UserRole.admin)
    make_user(email="a2@test.local", role=UserRole.admin)
    make_user(email="disabled@test.local", role=UserRole.admin, is_disabled=True)
    make_user(email="client@test.local", role=UserRole.client)
    _enable(db)
    res = error_alert.handle_error_event(db, _http_event())
    assert res["status"] == "sent"
    assert res["recipients"] == 2  # only the two enabled admins
    assert db.query(Notification).filter(
        Notification.category == NotificationCategory.server_error
    ).count() == 2


def test_custom_mode_enqueues_send_per_address_no_inapp(db, fake_redis, monkeypatch):
    _enable(
        db,
        **{
            K.ERROR_ALERT_RECIPIENTS_MODE: "custom",
            K.ERROR_ALERT_CUSTOM_RECIPIENTS: "ops@corp.local,sre@corp.local",
        },
    )
    sent = []
    from app.services import job_queue
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: sent.append((name, kw)))
    res = error_alert.handle_error_event(db, _http_event())
    assert res["status"] == "sent"
    assert res["recipients"] == 2
    tos = sorted(kw["to"] for name, kw in sent if name == "send_email_job")
    assert tos == ["ops@corp.local", "sre@corp.local"]
    # Custom addresses aren't users -> no in-app bell rows.
    assert db.query(Notification).filter(
        Notification.category == NotificationCategory.server_error
    ).count() == 0


# --- fail-open -------------------------------------------------------------


def test_redis_down_still_sends(db, make_user, monkeypatch):
    make_user(email="a@test.local", role=UserRole.admin)
    _enable(db)

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(error_alert, "get_redis", _boom)
    # Cooldown errs toward sending (the hourly cap, which conftest stubs to
    # allow, is the bound on a real outage).
    assert error_alert.handle_error_event(db, _http_event())["status"] == "sent"


# --- handler 4xx filter ----------------------------------------------------


def _fake_request():
    return types.SimpleNamespace(
        method="GET",
        url=types.SimpleNamespace(path="/api/files/9"),
        state=types.SimpleNamespace(request_id="rid", user_id=7, auth_via="session"),
    )


def test_handler_skips_4xx_enqueues_5xx(monkeypatch):
    from app.middleware import errors
    from app.services import job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))

    errors._maybe_enqueue_error_alert(
        _fake_request(), status_code=404, code="NOT_FOUND", exc=Exception("nope")
    )
    assert calls == []  # 4xx never alerts

    errors._maybe_enqueue_error_alert(
        _fake_request(), status_code=500, code="INTERNAL_ERROR", exc=Exception("boom")
    )
    assert len(calls) == 1
    name, kw = calls[0]
    assert name == "notify_admin_error"
    assert kw["event"]["status_code"] == 500
    assert kw["event"]["source"] == "http"
    assert kw["event"]["path"] == "/api/files/9"
