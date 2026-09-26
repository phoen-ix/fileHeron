"""Pre-update backup + infra sync: what the backend hands the updater.

The backend cannot see ./backups or the host checkout, so it only DECIDES:
whether this update backs up first (the dialog's checkbox, the choice stored
with a postponed update, or the admin default) and the registry values the
executor applies itself. It all rides in the job file's `options` block; the
executor re-validates every field (tests/infra/test_updater_infra_sync.py).
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.models.user import UserRole
from app.services import maintenance as maintenance_svc
from app.services import release_apply, settings_registry
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now

_DEFAULT_OPTIONS = {
    "backup": True,
    "backup_on_db_change": True,
    "backup_keep": 3,
    "backup_max_age_days": 30,
    "infra_sync": True,
}


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(release_apply, "STATE_DIR", state_dir)
    monkeypatch.setattr(release_apply, "STATE_FILE", state_dir / "current_job.json")
    monkeypatch.setattr(release_apply, "ROLLBACK_FILE", state_dir / "rollback_target.json")
    yield


def _job() -> dict:
    return json.loads(release_apply.STATE_FILE.read_text())


def _set(db, key: str, value: str) -> None:
    settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


# --- release_apply ------------------------------------------------------------


def test_apply_writes_the_options_block_only_when_given():
    release_apply.apply(action="update", target_tag="v1.0.1", options={"backup": False})
    assert _job()["options"] == {"backup": False}
    release_apply.STATE_FILE.unlink()
    release_apply.apply(action="update", target_tag="v1.0.1")
    assert "options" not in _job()


def test_job_options_reads_the_registry(db):
    assert release_apply.job_options(db, backup=True) == _DEFAULT_OPTIONS
    keys = settings_registry.K
    _set(db, keys.UPDATES_BACKUP_ON_DB_CHANGE, "false")
    _set(db, keys.UPDATES_BACKUP_KEEP, "7")
    _set(db, keys.UPDATES_BACKUP_MAX_AGE_DAYS, "0")
    _set(db, keys.UPDATES_INFRA_SYNC, "false")
    assert release_apply.job_options(db, backup=False) == {
        "backup": False,
        "backup_on_db_change": False,
        "backup_keep": 7,
        "backup_max_age_days": 0,
        "infra_sync": False,
    }


def test_the_retention_bounds_match_the_executors():
    """The executor re-validates with its own copy of these bounds
    (_OPTION_BOUNDS in run.py); a value the registry accepts but the executor
    rejects would be silently replaced by the executor's default."""
    import importlib.util
    from pathlib import Path

    run_py = Path(__file__).resolve().parents[2] / "docker" / "updater-executor" / "run.py"
    spec = importlib.util.spec_from_file_location("fh_updater_bounds", run_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for key, attr in (("backup_keep", "UPDATES_BACKUP_KEEP"), ("backup_max_age_days", "UPDATES_BACKUP_MAX_AGE_DAYS")):
        spec_ = settings_registry.BY_KEY[getattr(settings_registry.K, attr)]
        assert (spec_.min, spec_.max) == mod._OPTION_BOUNDS[key], key
        assert settings_registry.env_default(spec_) == mod._OPTION_DEFAULTS[key], key
    for key, attr in (("backup_on_db_change", "UPDATES_BACKUP_ON_DB_CHANGE"), ("infra_sync", "UPDATES_INFRA_SYNC")):
        spec_ = settings_registry.BY_KEY[getattr(settings_registry.K, attr)]
        assert settings_registry.env_default(spec_) is mod._OPTION_DEFAULTS[key], key


def test_get_job_passes_the_new_fields_through_and_sanitises_them():
    release_apply.apply(action="update", target_tag="v1.0.1")
    job = _job()
    job.update(
        status="restarting",
        phase="syncing_infra",
        backup_dir="backups/pre-update/2026-09-26_120000_v1.0.0-to-v1.0.1",
        warnings=["clamav did not come up healthy", 42, "x" * 900] + ["w"] * 30,
    )
    release_apply.STATE_FILE.write_text(json.dumps(job))
    got = release_apply.get_job(job["id"])
    assert got["phase"] == "syncing_infra"
    assert got["backup_dir"].startswith("backups/pre-update/")
    assert got["warnings"][0] == "clamav did not come up healthy"
    assert all(isinstance(w, str) and len(w) <= 500 for w in got["warnings"])
    assert len(got["warnings"]) == 20


def test_get_job_from_an_older_executor_has_empty_new_fields():
    release_apply.apply(action="update", target_tag="v1.0.1")
    job = _job()
    job["warnings"] = "not a list"
    release_apply.STATE_FILE.write_text(json.dumps(job))
    got = release_apply.get_job(job["id"])
    assert got["phase"] is None and got["backup_dir"] is None and got["warnings"] == []


# --- the admin routes -----------------------------------------------------------


async def _admin_headers(make_user, login_as, email):
    make_user(email=email, role=UserRole.admin)
    token, _cookies = await login_as(email, "TestPassword123!")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "sent", "expected"),
    [
        (None, None, True),      # no choice sent: the shipped default (on)
        ("false", None, False),  # no choice sent: the admin turned the default off
        ("false", True, True),   # the dialog's choice wins
        (None, False, False),
    ],
)
async def test_update_puts_the_backup_choice_in_the_job(
    client, db, make_user, login_as, setting, sent, expected
):
    if setting is not None:
        _set(db, settings_registry.K.UPDATES_BACKUP_DEFAULT, setting)
    headers = await _admin_headers(make_user, login_as, f"adm-bk-{setting}-{sent}@test.local")
    body = {"password": "TestPassword123!", "target_tag": "v1.0.1"}
    if sent is not None:
        body["backup"] = sent
    r = await client.post("/api/admin/system/update", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert _job()["options"] == {**_DEFAULT_OPTIONS, "backup": expected}


@pytest.mark.asyncio
async def test_a_postponed_update_keeps_the_choice_and_update_now_may_override_it(
    client, db, make_user, login_as
):
    headers = await _admin_headers(make_user, login_as, "adm-bk-pp@test.local")
    r = await client.post(
        "/api/admin/system/update",
        json={"password": "TestPassword123!", "target_tag": "v1.0.1", "postpone": True, "backup": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert maintenance_svc.get_pending_update(db)["backup"] is False

    # The SPA reads the stored choice back to pre-set the "Update now" dialog;
    # response_model would silently drop an undeclared key.
    r = await client.get("/api/admin/system/transfer-activity", headers=headers)
    assert r.json()["pending_update"]["backup"] is False

    r = await client.post(
        "/api/admin/system/update/now",
        json={"password": "TestPassword123!", "backup": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert _job()["options"]["backup"] is True


def test_the_drain_path_uses_the_stored_choice(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        release_apply, "apply",
        lambda *, action, target_tag, options=None: calls.append(options)
        or {"job_id": "j", "action": action, "target_tag": target_tag},
    )
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(
        db, {"target_tag": "v9.9.9", "deadline_iso": "x", "backup": False}, actor=None
    )
    db.commit()
    maintenance_svc.apply_pending_update(db, reason="drain")
    assert calls[0]["backup"] is False


@pytest.mark.asyncio
async def test_update_status_reports_the_backup_settings(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as, "adm-bk-st@test.local")
    r = await client.get("/api/admin/system/update-status", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["backup_default"] is True
    assert r.json()["backup_on_db_change"] is True

    _set(db, settings_registry.K.UPDATES_BACKUP_DEFAULT, "false")
    _set(db, settings_registry.K.UPDATES_BACKUP_ON_DB_CHANGE, "false")
    r = await client.get("/api/admin/system/update-status", headers=headers)
    assert r.json()["backup_default"] is False
    assert r.json()["backup_on_db_change"] is False


# --- the drain worker ---------------------------------------------------------


def test_the_stale_hand_off_lift_waits_for_a_running_update(db):
    """A backup plus a MariaDB major upgrade can outlast HANDOFF_STALE_MIN; the
    gate must stay shut while the updater's job is still in flight."""
    from app.workers import drain_pending_update as mod

    old = (utc_now() - timedelta(minutes=maintenance_svc.HANDOFF_STALE_MIN + 5)).isoformat()
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_handoff_at(db, old)
    db.commit()
    release_apply.apply(action="update", target_tag="v1.0.1")
    job = _job()
    job["status"] = "restarting"
    release_apply.STATE_FILE.write_text(json.dumps(job))

    assert mod._lift_if_stale(db) is False
    assert maintenance_svc.is_enabled(db) is True

    job["status"] = "failed"
    release_apply.STATE_FILE.write_text(json.dumps(job))
    assert mod._lift_if_stale(db) is True
    assert maintenance_svc.is_enabled(db) is False
