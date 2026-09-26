"""Automatic updates: what the daily `auto_update` task schedules, the gate on
turning it on, and how a handed-off update's outcome is reported.

The task never applies anything itself - it writes the same pending record
"Postpone" does, and `drain_pending_update` takes it from there - so these tests
stop at the hand-off with release_apply.apply stubbed, and drive the outcome
through the real job file.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app import version as version_mod
from app.models.audit_log import AuditEventType, AuditLog
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import auto_update as au
from app.services import maintenance as maintenance_svc
from app.services import release_apply, settings_registry
from app.services import settings as settings_svc
from app.services.release_check import CacheKeys
from app.utils.timeutil import utc_now

K = settings_svc.Keys
PASSWORD = "TestPassword123!"


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(release_apply, "STATE_DIR", state_dir)
    monkeypatch.setattr(release_apply, "STATE_FILE", state_dir / "current_job.json")
    monkeypatch.setattr(release_apply, "ROLLBACK_FILE", state_dir / "rollback_target.json")


@pytest.fixture(autouse=True)
def _running(monkeypatch):
    monkeypatch.setattr(version_mod, "VERSION", "v2.19.1")


def _set(db, key: str, value: str | None) -> None:
    settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


def _cache(db, latest: str | None, *, published_h_ago: float | None = 48, checked_h_ago: float | None = 1):
    now = utc_now()
    _set(db, CacheKeys.LATEST_VERSION, latest)
    _set(db, CacheKeys.LATEST_PUBLISHED_AT,
         None if published_h_ago is None
         else (now - timedelta(hours=published_h_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _set(db, CacheKeys.LAST_SUCCESS_AT,
         None if checked_h_ago is None else (now - timedelta(hours=checked_h_ago)).isoformat())


def _enable(db, scope: str = "patch", min_age: int = 24) -> None:
    _set(db, K.UPDATES_AUTO_ENABLED, "true")
    _set(db, K.UPDATES_AUTO_SCOPE, scope)
    _set(db, K.UPDATES_AUTO_MIN_AGE_HOURS, str(min_age))


def _audit(db, event: AuditEventType) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.event_type == event.value).all()


def _admin(make_user, email: str = "auto-admin@test.local"):
    return make_user(email=email, role=UserRole.admin)


# --- settings --------------------------------------------------------------------


def test_it_ships_off_patch_only_after_24_hours(db):
    s = au.get_settings(db)
    assert (s.enabled, s.scope, s.min_age_hours) == (False, "patch", 24)


def test_stored_values_are_clamped(db):
    _set(db, K.UPDATES_AUTO_SCOPE, "everything")
    _set(db, K.UPDATES_AUTO_MIN_AGE_HOURS, "99999")
    assert au.get_settings(db).scope == "patch"
    assert au.get_settings(db).min_age_hours == au.MIN_AGE_HOURS_MAX
    _set(db, K.UPDATES_AUTO_MIN_AGE_HOURS, "-5")
    assert au.get_settings(db).min_age_hours == 0


def test_the_keys_are_not_registry_tunables():
    """/settings/advanced has no step-up; a registry key would let any admin
    session turn automatic updates on without the password."""
    auto_keys = {K.UPDATES_AUTO_ENABLED, K.UPDATES_AUTO_SCOPE, K.UPDATES_AUTO_MIN_AGE_HOURS}
    assert not auto_keys & set(settings_registry.BY_KEY)


# --- eligibility -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "latest", "reason"),
    [
        ("patch", "v2.19.2", "eligible"),
        ("patch", "v2.20.0", "out_of_scope"),
        ("patch", "v3.0.0", "out_of_scope"),
        ("minor", "v2.20.0", "eligible"),
        ("minor", "v3.0.0", "out_of_scope"),
        ("any", "v3.0.0", "eligible"),
        ("any", "v2.19.1", "up_to_date"),
        ("any", "v2.18.9", "up_to_date"),
    ],
)
def test_scope(db, scope, latest, reason):
    _cache(db, latest)
    tag, why = au.eligible_target(db, au.AutoUpdateSettings(True, scope, 24))
    assert why == reason
    assert tag == (latest if reason == "eligible" else None)


@pytest.mark.parametrize(("hours_ago", "reason"), [(23.5, "too_new"), (24.5, "eligible")])
def test_the_wait_after_publication(db, hours_ago, reason):
    _cache(db, "v2.19.2", published_h_ago=hours_ago)
    assert au.eligible_target(db, au.AutoUpdateSettings(True, "patch", 24))[1] == reason


def test_no_wait_installs_a_fresh_release(db):
    _cache(db, "v2.19.2", published_h_ago=0.1)
    assert au.eligible_target(db, au.AutoUpdateSettings(True, "patch", 0))[0] == "v2.19.2"


@pytest.mark.parametrize("checked", [49, None])
def test_a_stale_release_cache_installs_nothing(db, checked):
    _cache(db, "v2.19.2", checked_h_ago=checked)
    assert au.eligible_target(db, au.get_settings(db))[1] == "release_check_stale"


def test_an_unknown_publication_time_installs_nothing(db):
    _cache(db, "v2.19.2", published_h_ago=None)
    assert au.eligible_target(db, au.get_settings(db))[1] == "publication_time_unknown"


def test_a_dev_build_is_never_auto_updated(db, monkeypatch):
    monkeypatch.setattr(version_mod, "VERSION", "0.0.0-dev")
    _cache(db, "v2.19.2")
    assert au.eligible_target(db, au.AutoUpdateSettings(True, "any", 0))[1] == "not_a_release_build"


def test_a_release_that_failed_automatically_is_not_retried(db):
    _cache(db, "v2.19.2")
    _set(db, K.UPDATES_AUTO_SKIP_TAG, "v2.19.2")
    assert au.eligible_target(db, au.get_settings(db))[1] == "skipped_after_failure"
    # ...but a newer one is.
    _cache(db, "v2.19.3")
    assert au.eligible_target(db, au.get_settings(db))[0] == "v2.19.3"


# --- schedule(): what the daily task does -----------------------------------------------


def test_disabled_schedules_nothing(db):
    _cache(db, "v2.19.2")
    assert au.schedule(db) == {"scheduled": False, "reason": "disabled"}
    assert maintenance_svc.get_pending_update(db) is None
    assert maintenance_svc.is_enabled(db) is False


def test_it_schedules_exactly_like_postpone(db, make_user):
    admin = _admin(make_user)
    _enable(db)
    _cache(db, "v2.19.2")
    out = au.schedule(db)
    assert out["scheduled"] is True and out["target_tag"] == "v2.19.2"
    pending = maintenance_svc.get_pending_update(db)
    assert pending["target_tag"] == "v2.19.2"
    assert pending["origin"] == "auto" and pending["requested_by_id"] is None
    assert pending["backup"] is True  # updates.backup_default
    assert pending["deadline_iso"] == out["deadline_iso"]
    assert maintenance_svc.is_enabled(db) is True
    rows = _audit(db, AuditEventType.update_auto_scheduled)
    assert len(rows) == 1 and rows[0].actor_user_id is None
    note = db.query(Notification).filter(Notification.user_id == admin.id).one()
    assert note.payload_json["reason"] == "update_auto_scheduled"
    assert note.payload_json["target_tag"] == "v2.19.2"


def test_the_backup_follows_the_admin_default(db):
    _enable(db)
    _cache(db, "v2.19.2")
    _set(db, settings_registry.K.UPDATES_BACKUP_DEFAULT, "false")
    au.schedule(db)
    assert maintenance_svc.get_pending_update(db)["backup"] is False


def test_it_waits_for_a_job_in_flight(db):
    _enable(db)
    _cache(db, "v2.19.2")
    release_apply.apply(action="update", target_tag="v2.19.2")
    assert au.schedule(db)["reason"] == "job_in_progress"
    assert maintenance_svc.get_pending_update(db) is None


def test_it_leaves_an_admins_postponed_update_alone(db):
    _enable(db)
    _cache(db, "v2.19.2")
    record = {"target_tag": "v2.19.2", "deadline_iso": "x", "requested_by_id": 1, "origin": "admin"}
    maintenance_svc.set_pending_update(db, record, actor=None)
    db.commit()
    assert au.schedule(db)["reason"] == "update_already_pending"
    assert maintenance_svc.get_pending_update(db) == record


def test_it_does_not_ride_on_maintenance_an_operator_turned_on(db):
    """The new container would lift it on boot - the operator's DB restore
    or storage migration would lose its gate."""
    _enable(db)
    _cache(db, "v2.19.2")
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    assert au.schedule(db)["reason"] == "maintenance_on"
    assert maintenance_svc.get_pending_update(db) is None


@pytest.mark.asyncio
async def test_the_task_reports_a_failure_instead_of_raising(db, monkeypatch):
    """A raise is retried max_tries times; the cron convention is CRON_FAILED_KEY."""
    from app.services.cron_tracker import CRON_FAILED_KEY
    from app.workers import auto_update as task

    monkeypatch.setattr(task, "SessionLocal", lambda: db)
    monkeypatch.setattr(task.auto_update_svc, "schedule", lambda _db: 1 / 0)
    out = await task.auto_update.__wrapped__(None)  # the body, without cron_runs bookkeeping
    assert out[CRON_FAILED_KEY] is True and "ZeroDivisionError" in out["error"]


# --- the hand-off and its outcome --------------------------------------------------------


def _hand_off(db, monkeypatch, *, origin: str = "auto") -> str:
    """Schedule, then let the drain fire it against the real job file."""
    if origin == "auto":
        _enable(db)
        _cache(db, "v2.19.2")
        assert au.schedule(db)["scheduled"] is True
    else:
        maintenance_svc.schedule_pending_update(
            db, target_tag="v2.19.2", backup=True, requested_by=None, origin="admin",
        )
        db.commit()
    result = maintenance_svc.apply_pending_update(db, reason="drain")
    assert result is not None
    return result["job_id"]


def _finish(job_id: str, status: str, error: str | None = None) -> None:
    job = json.loads(release_apply.STATE_FILE.read_text())
    assert job["id"] == job_id
    job.update(status=status, error=error)
    release_apply.STATE_FILE.write_text(json.dumps(job))


def test_the_hand_off_remembers_the_job_and_its_origin(db, monkeypatch):
    job_id = _hand_off(db, monkeypatch)
    assert maintenance_svc.get_handoff_job(db) == {
        "job_id": job_id, "target_tag": "v2.19.2", "origin": "auto",
    }
    triggered = _audit(db, AuditEventType.update_triggered)
    assert triggered[-1].extra["origin"] == "auto"


def test_nothing_is_reported_while_the_job_runs(db, monkeypatch):
    job_id = _hand_off(db, monkeypatch)
    assert maintenance_svc.report_handoff_outcome(db) is None  # queued
    _finish(job_id, "restarting")
    assert maintenance_svc.report_handoff_outcome(db) is None
    assert maintenance_svc.get_handoff_job(db) is not None


def test_a_healthy_update_is_reported_once(db, make_user, monkeypatch):
    admin = _admin(make_user)
    job_id = _hand_off(db, monkeypatch)
    _finish(job_id, "healthy")
    assert maintenance_svc.report_handoff_outcome(db) == "healthy"
    assert maintenance_svc.report_handoff_outcome(db) is None
    rows = _audit(db, AuditEventType.update_completed)
    assert len(rows) == 1 and rows[0].target_id == job_id
    assert settings_svc.get(db, K.UPDATES_AUTO_SKIP_TAG) is None
    reasons = [n.payload_json["reason"] for n in db.query(Notification).filter(Notification.user_id == admin.id)]
    assert reasons.count("update_completed") == 1


@pytest.mark.parametrize("status", ["rolled_back", "failed"])
def test_an_automatic_update_that_failed_is_skipped_from_then_on(db, make_user, monkeypatch, status):
    admin = _admin(make_user)
    job_id = _hand_off(db, monkeypatch)
    _finish(job_id, status, error="backend health check timed out")
    assert maintenance_svc.report_handoff_outcome(db) == status
    row = _audit(db, AuditEventType.update_failed)[0]
    assert row.extra["error"] == "backend health check timed out"
    assert settings_svc.get(db, K.UPDATES_AUTO_SKIP_TAG) == "v2.19.2"
    note = [n for n in db.query(Notification).filter(Notification.user_id == admin.id)
            if n.payload_json.get("reason") == "update_failed"]
    assert len(note) == 1 and "not be retried" in note[0].payload_json["detail"]
    assert au.eligible_target(db, au.get_settings(db))[1] == "skipped_after_failure"


def test_a_failed_postponed_update_is_reported_but_not_skipped(db, monkeypatch):
    """The skip belongs to the automatic updater; an admin's own postponed
    update that failed is theirs to retry."""
    job_id = _hand_off(db, monkeypatch, origin="admin")
    _finish(job_id, "failed")
    assert maintenance_svc.report_handoff_outcome(db) == "failed"
    assert len(_audit(db, AuditEventType.update_failed)) == 1
    assert settings_svc.get(db, K.UPDATES_AUTO_SKIP_TAG) is None


def test_a_job_the_file_no_longer_holds_is_dropped_silently(db, monkeypatch):
    _hand_off(db, monkeypatch)
    release_apply.STATE_FILE.unlink()
    release_apply.apply(action="update", target_tag="v2.19.3")  # a newer job took the file
    assert maintenance_svc.report_handoff_outcome(db) is None
    assert maintenance_svc.get_handoff_job(db) is None
    assert not _audit(db, AuditEventType.update_completed) + _audit(db, AuditEventType.update_failed)


@pytest.mark.asyncio
async def test_the_drain_worker_reports_the_outcome(db, monkeypatch):
    from app.workers import drain_pending_update as drain

    job_id = _hand_off(db, monkeypatch)
    _finish(job_id, "healthy")
    monkeypatch.setattr(drain, "SessionLocal", lambda: db)
    out = await drain.drain_pending_update(None)
    assert out["reported"] == "healthy"
    assert len(_audit(db, AuditEventType.update_completed)) == 1


@pytest.mark.asyncio
async def test_a_reporting_failure_never_stops_the_drain(db, monkeypatch):
    from app.workers import drain_pending_update as drain

    def boom(_db):
        raise RuntimeError("job file unreadable")

    monkeypatch.setattr(drain.maintenance_svc, "report_handoff_outcome", boom)
    monkeypatch.setattr(drain, "SessionLocal", lambda: db)
    out = await drain.drain_pending_update(None)
    assert out["pending"] is False and "reported" not in out


# --- the routes -------------------------------------------------------------------------


async def _headers(make_user, login_as, email="auto-route@test.local"):
    _admin(make_user, email)
    token, _cookies = await login_as(email, PASSWORD)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_the_settings_route_reads_the_defaults(client, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.get("/api/admin/settings/auto-update", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False, "scope": "patch", "min_age_hours": 24, "skipped_tag": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("password", [None, "wrong-password"])
async def test_turning_it_on_needs_the_password(client, db, make_user, login_as, password):
    h = await _headers(make_user, login_as, f"auto-pw-{password}@test.local")
    body: dict = {"enabled": True}
    if password:
        body["password"] = password
    r = await client.put("/api/admin/settings/auto-update", json=body, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INVALID_PASSWORD"
    assert au.get_settings(db).enabled is False
    assert _audit(db, AuditEventType.step_up_failed)


@pytest.mark.asyncio
async def test_turning_it_on_with_the_password(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.put(
        "/api/admin/settings/auto-update",
        json={"enabled": True, "scope": "minor", "min_age_hours": 72, "password": PASSWORD},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "scope": "minor", "min_age_hours": 72, "skipped_tag": None}
    assert au.get_settings(db) == au.AutoUpdateSettings(True, "minor", 72)
    row = _audit(db, AuditEventType.updates_settings_changed)[-1]
    assert row.extra["auto_update"] == {"enabled": True, "scope": "minor", "min_age_hours": 72}
    assert PASSWORD not in json.dumps(row.extra)


@pytest.mark.asyncio
async def test_widening_it_while_on_needs_the_password_too(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    _enable(db)
    r = await client.put("/api/admin/settings/auto-update", json={"scope": "any"}, headers=h)
    assert r.status_code == 403
    assert au.get_settings(db).scope == "patch"
    r = await client.put("/api/admin/settings/auto-update", json={"min_age_hours": 0}, headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_turning_it_off_or_editing_it_while_off_needs_no_password(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    _enable(db)
    r = await client.put("/api/admin/settings/auto-update", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.put("/api/admin/settings/auto-update", json={"scope": "minor"}, headers=h)
    assert r.status_code == 200 and r.json()["scope"] == "minor"


@pytest.mark.asyncio
async def test_a_no_op_while_on_needs_no_password(client, db, make_user, login_as):
    """Re-saving the unchanged form must not demand the password."""
    h = await _headers(make_user, login_as)
    _enable(db)
    r = await client.put(
        "/api/admin/settings/auto-update",
        json={"enabled": True, "scope": "patch", "min_age_hours": 24}, headers=h,
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"scope": "all"}, {"min_age_hours": 721}, {"min_age_hours": -1}])
async def test_invalid_values_are_refused(client, make_user, login_as, body):
    h = await _headers(make_user, login_as)
    r = await client.put("/api/admin/settings/auto-update", json=body, headers=h)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_the_advanced_settings_route_cannot_turn_it_on(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.put(
        "/api/admin/settings/advanced", json={"updates": {K.UPDATES_AUTO_ENABLED: True}}, headers=h,
    )
    assert r.status_code == 400
    assert au.get_settings(db).enabled is False


@pytest.mark.asyncio
async def test_non_admins_cannot_touch_it(client, make_user, login_as):
    make_user(email="auto-emp@test.local", role=UserRole.employee)
    token, _ = await login_as("auto-emp@test.local", PASSWORD)
    h = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/admin/settings/auto-update", headers=h)).status_code == 403
    r = await client.put("/api/admin/settings/auto-update", json={"enabled": False}, headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_status_shows_it_with_its_schedule(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.get("/api/admin/system/update-status", headers=h)
    assert r.json()["auto_update"] == {
        "enabled": False, "scope": "patch", "min_age_hours": 24, "schedule_enabled": True,
        "schedule_kind": "daily", "daily_time": "03:30", "interval_minutes": 60, "skipped_tag": None,
    }
    _enable(db, "any", 0)
    _set(db, "cron.auto_update.daily_time", "04:45")
    _set(db, "cron.auto_update.enabled", "false")
    _set(db, K.UPDATES_AUTO_SKIP_TAG, "v2.19.2")
    got = (await client.get("/api/admin/system/update-status", headers=h)).json()["auto_update"]
    assert (got["enabled"], got["scope"], got["min_age_hours"]) == (True, "any", 0)
    assert (got["daily_time"], got["schedule_enabled"], got["skipped_tag"]) == ("04:45", False, "v2.19.2")


@pytest.mark.asyncio
async def test_transfer_activity_carries_an_automatic_pending_update(client, db, make_user, login_as):
    """PendingUpdate.requested_by_id was a required int: an automatic record
    has none, and response validation would have 500'd the page."""
    h = await _headers(make_user, login_as)
    _enable(db)
    _cache(db, "v2.19.2")
    au.schedule(db)
    r = await client.get("/api/admin/system/transfer-activity", headers=h)
    assert r.status_code == 200, r.text
    pending = r.json()["pending_update"]
    assert pending["origin"] == "auto" and pending["requested_by_id"] is None


@pytest.mark.asyncio
async def test_postpone_still_writes_the_admins_record(client, db, make_user, login_as):
    """Control for the extraction into maintenance.schedule_pending_update."""
    admin = _admin(make_user, "auto-pp@test.local")
    token, _ = await login_as("auto-pp@test.local", PASSWORD)
    r = await client.post(
        "/api/admin/system/update",
        json={"password": PASSWORD, "target_tag": "v2.19.2", "postpone": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    pending = maintenance_svc.get_pending_update(db)
    assert pending["requested_by_id"] == admin.id and pending["origin"] == "admin"
    assert pending["deadline_iso"] == r.json()["deadline_iso"]
    assert _audit(db, AuditEventType.update_postponed)
    assert not _audit(db, AuditEventType.update_auto_scheduled)


# --- config backup ------------------------------------------------------------------------


def test_config_backup_keeps_the_setting_but_not_the_runtime_state():
    from app.services import config_backup

    transient = config_backup._TRANSIENT_SETTING_KEYS
    assert {K.UPDATES_AUTO_SKIP_TAG, K.MAINTENANCE_HANDOFF_JOB} <= transient
    assert not {K.UPDATES_AUTO_ENABLED, K.UPDATES_AUTO_SCOPE, K.UPDATES_AUTO_MIN_AGE_HOURS} & transient
