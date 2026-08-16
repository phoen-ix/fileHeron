"""services/error_alert.py - saferails + send for the email-on-server-error
feature. Covers signature dedup, cooldown, hourly cap, source/master gates,
recipient modes, the 4xx filter in the handler helper, and Redis fail-open."""
from __future__ import annotations

import types
from datetime import timedelta

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


def _http_event(path="/api/files/123", code="INTERNAL_ERROR", status=500, exc_type="ValueError", ip="203.0.113.7"):
    return {
        "source": "http",
        "exception_type": exc_type,
        "message": "boom",
        "method": "GET",
        "path": path,
        "status_code": status,
        "code": code,
        "ip": ip,
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
    assert captured["ip"] == "203.0.113.7"  # client IP flows into the email payload


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
        client=types.SimpleNamespace(host="203.0.113.7"),
        state=types.SimpleNamespace(request_id="rid", user_id=7, auth_via="session"),
    )


def test_middleware_skips_4xx_by_default_enqueues_5xx(monkeypatch):
    from app.middleware import errors
    from app.services import error_log, job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))
    # 4xx capture off (default) -> the cheap cached gate says skip.
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: False)

    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=404, code="NOT_FOUND", exc=Exception("nope")
    )
    assert calls == []  # 4xx not captured by default

    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=500, code="INTERNAL_ERROR", exc=Exception("boom")
    )
    assert len(calls) == 1
    name, kw = calls[0]
    assert name == "notify_admin_error"
    assert kw["event"]["status_code"] == 500
    assert kw["event"]["source"] == "http"
    assert kw["event"]["path"] == "/api/files/9"


def test_middleware_skips_never_capture_codes(monkeypatch):
    from app.middleware import errors
    from app.services import error_log, job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    monkeypatch.setattr(error_log, "capture_rate_per_min_cached", lambda: 100)

    # JOB_NOT_FOUND = the self-update UI polling its own vanished job; never logged.
    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=404, code="JOB_NOT_FOUND", exc=Exception("gone")
    )
    assert calls == []
    # A normal 404 still captures.
    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=404, code="NOT_FOUND", exc=Exception("nope")
    )
    assert len(calls) == 1


def test_expired_access_token_is_not_captured_but_its_siblings_are(monkeypatch):
    """TOKEN_EXPIRED is the 15-minute access token reaching exp: emitted once per
    token lifetime per open tab by the SSE re-mint, always followed by a successful
    refresh and replay. Left capturing it drowned everything else in the log (32 of
    41 rows on one day, on a four-user instance).

    The half that gives the suppression its meaning is the second one: it is keyed
    on the CODE, so the other 401s - including the AUTH_REQUIRED that surfaced the
    ungated admin SSE route - keep capturing. Dropping 401 from the allowlist
    instead would have taken those with it."""
    from app.middleware import errors
    from app.services import error_log, job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    monkeypatch.setattr(error_log, "capture_rate_per_min_cached", lambda: 100)

    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=401, code="TOKEN_EXPIRED", exc=Exception("expired")
    )
    assert calls == [], "the reactive-refresh 401 must not reach the error log"

    # Every other 401 still captures - same status, different code.
    for code in ("AUTH_REQUIRED", "INVALID_CREDENTIALS", "TOTP_REQUIRED", "SESSION_REVOKED"):
        errors._maybe_enqueue_error_event(
            _fake_request(), status_code=401, code=code, exc=Exception(code)
        )
    assert [kw["event"]["code"] for _, kw in calls] == [
        "AUTH_REQUIRED",
        "INVALID_CREDENTIALS",
        "TOTP_REQUIRED",
        "SESSION_REVOKED",
    ]


def test_expired_access_token_really_raises_the_suppressed_code():
    """The suppression above is keyed on the literal string TOKEN_EXPIRED, and
    nothing else in the suite pins that the expiry path produces it - so a rename
    would silently restore the flood with no test going red.

    Assert on what the code PRODUCES: mint a real token with a past exp and put it
    through the real resolver, rather than re-deriving the string from the source."""
    import jwt

    from app.config import settings
    from app.middleware.errors import _NEVER_CAPTURE_CODES, AppError
    from app.services.jwt_session import resolve_user_from_access_token
    from app.utils.timeutil import utc_now_aware

    expired = utc_now_aware() - timedelta(minutes=1)
    token = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": int((expired - timedelta(minutes=15)).timestamp()),
            "exp": int(expired.timestamp()),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(AppError) as exc:
        resolve_user_from_access_token(None, token, settings)
    assert exc.value.status_code == 401
    assert exc.value.code == "TOKEN_EXPIRED"
    assert exc.value.code in _NEVER_CAPTURE_CODES


def test_middleware_enqueues_4xx_when_capture_on(monkeypatch):
    from app.middleware import errors
    from app.services import error_log, job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    monkeypatch.setattr(error_log, "capture_rate_per_min_cached", lambda: 100)

    errors._maybe_enqueue_error_event(
        _fake_request(), status_code=429, code="RATE_LIMITED", exc=Exception("slow down")
    )
    assert len(calls) == 1
    assert calls[0][1]["event"]["status_code"] == 429
    assert calls[0][1]["event"]["code"] == "RATE_LIMITED"


def _http_4xx_event(status=429, code="RATE_LIMITED"):
    ev = _http_event(status=status, code=code)
    return ev


def test_4xx_alert_requires_source_and_allowlist(db, make_user, fake_redis):
    make_user(email="a@test.local", role=UserRole.admin)
    _enable(db)  # master on; 4xx alert source defaults off
    ev = _http_4xx_event(status=429, code="RATE_LIMITED")
    # 4xx alert source off -> not sent (but it IS logged; see test_error_log).
    assert error_alert.handle_error_event(db, ev)["status"] == "source_disabled"
    # Turn the 4xx alert source on + allowlist 429.
    settings_svc.set_value(db, key=K.ERROR_ALERT_SOURCE_HTTP_4XX, value="true", actor=None)
    settings_svc.set_value(db, key=K.ERROR_LOG_4XX_CODES, value="429", actor=None)
    db.commit()
    assert error_alert.handle_error_event(db, ev)["status"] == "sent"
    # A 4xx code NOT in the allowlist still won't alert.
    ev409 = _http_4xx_event(status=409, code="CONFLICT")
    assert error_alert.handle_error_event(db, ev409)["status"] == "source_disabled"


# --- ops alerts (systemd OnFailure=) ---------------------------------------
#
# send_to_configured_recipients is the entry point backend/scripts/send_ops_alert.py
# uses, which scripts/ops/notify_failure.sh invokes from systemd's OnFailure=.
# It shares the recipient policy with HTTP error alerting but deliberately NOT
# the enable/cooldown/cap machinery.


def _ops_payload(code="BACKUP_FAILED", unit="fileheron-backup.service"):
    return {
        "source": "ops",
        "exception_type": None,
        "message": f"{unit} failed",
        "method": None,
        "path": None,
        "job_name": unit,
        "status_code": None,
        "code": code,
        "at": utc_now().isoformat(),
        "occurrences": 1,
    }


def test_ops_alert_reaches_admins_even_when_error_alerting_is_off(db, make_user):
    """The whole point: a failed backup must notify whether or not an admin has
    switched on email-on-server-error. Gating this on error_alert.enabled would
    make the backup silent on a stock install, which is the default."""
    make_user(email="a1@test.local", role=UserRole.admin)
    make_user(email="a2@test.local", role=UserRole.admin)
    make_user(email="off@test.local", role=UserRole.admin, is_disabled=True)
    make_user(email="c@test.local", role=UserRole.client)
    # error_alert.enabled deliberately left at its default (off).
    assert settings_svc.get(db, K.ERROR_ALERT_ENABLED) in (None, "", "false")

    sent = error_alert.send_to_configured_recipients(db, _ops_payload())

    assert sent == 2  # enabled admins only
    assert db.query(Notification).filter(
        Notification.category == NotificationCategory.server_error
    ).count() == 2


def test_ops_alert_honours_custom_recipients(db, monkeypatch):
    settings_svc.set_value(db, key=K.ERROR_ALERT_RECIPIENTS_MODE, value="custom", actor=None)
    settings_svc.set_value(
        db, key=K.ERROR_ALERT_CUSTOM_RECIPIENTS, value="ops@corp.local", actor=None
    )
    db.commit()
    queued = []
    from app.services import job_queue

    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: queued.append(kw))

    sent = error_alert.send_to_configured_recipients(db, _ops_payload())

    assert sent == 1
    assert [kw["to"] for kw in queued] == ["ops@corp.local"]


def test_ops_alert_reports_zero_when_nobody_would_be_told(db):
    """send_ops_alert.py exits non-zero on 0 so the alert unit itself goes
    `failed` rather than silently reaching nobody."""
    settings_svc.set_value(db, key=K.ERROR_ALERT_RECIPIENTS_MODE, value="custom", actor=None)
    settings_svc.set_value(db, key=K.ERROR_ALERT_CUSTOM_RECIPIENTS, value="", actor=None)
    db.commit()

    assert error_alert.send_to_configured_recipients(db, _ops_payload()) == 0


def test_no_recipients_path_reports_cleanly(db, fake_redis):
    """The zero-recipients branch logs and returns a status. It had no test, so
    a refactor that dropped the local it interpolated went green anyway - ruff
    caught the NameError, not the suite."""
    _enable(
        db,
        **{K.ERROR_ALERT_RECIPIENTS_MODE: "custom", K.ERROR_ALERT_CUSTOM_RECIPIENTS: ""},
    )
    res = error_alert.handle_error_event(db, _http_event())
    assert res["status"] == "no_recipients"
    assert res["recipients"] == 0
