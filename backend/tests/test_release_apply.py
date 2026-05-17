"""v1.0.0 self-update: backend writes update requests to a state file
that the shim container polls. Replaces the v0.x HMAC-over-HTTP design.

Tests cover:
- apply() writes the right JSON shape to /state/current_job.json
- apply() raises 409 when a job is in flight
- apply(rollback) reads the rollback target file
- get_version() reports current_tag + rollback_target + in-flight flag
- POST /admin/system/update enforces password re-prompt + audit + admin notify
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import release_apply


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    """Each test gets its own /state dir so writes don't leak between tests."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(release_apply, "STATE_DIR", state_dir)
    monkeypatch.setattr(release_apply, "STATE_FILE", state_dir / "current_job.json")
    monkeypatch.setattr(
        release_apply, "ROLLBACK_FILE", state_dir / "rollback_target.json"
    )
    yield


def test_apply_writes_state_file():
    """A fresh update request lands as a `pending` JSON record."""
    result = release_apply.apply(action="update", target_tag="v1.0.1")
    assert result["action"] == "update"
    assert result["target_tag"] == "v1.0.1"
    assert result["job_id"]

    raw = release_apply.STATE_FILE.read_text()
    parsed = json.loads(raw)
    assert parsed["status"] == "pending"
    assert parsed["target_tag"] == "v1.0.1"
    assert parsed["action"] == "update"
    assert parsed["id"] == result["job_id"]


def test_apply_refuses_when_in_flight():
    """A second apply during a pending/running job → 409 UPDATE_IN_PROGRESS."""
    from app.middleware.errors import AppError
    release_apply.apply(action="update", target_tag="v1.0.1")

    with pytest.raises(AppError) as exc:
        release_apply.apply(action="update", target_tag="v1.0.2")
    assert exc.value.status_code == 409
    assert exc.value.code == "UPDATE_IN_PROGRESS"


def test_apply_after_healthy_succeeds():
    """Once a job is in terminal state, a new apply replaces it."""
    release_apply.apply(action="update", target_tag="v1.0.1")
    # Simulate executor finishing the job.
    state = json.loads(release_apply.STATE_FILE.read_text())
    state["status"] = "healthy"
    release_apply.STATE_FILE.write_text(json.dumps(state))

    # New apply must succeed.
    result = release_apply.apply(action="update", target_tag="v1.0.2")
    assert result["target_tag"] == "v1.0.2"


def test_rollback_reads_target_file():
    """action=rollback uses the tag from rollback_target.json."""
    release_apply.ROLLBACK_FILE.write_text(json.dumps({"tag": "v0.9.0"}))
    result = release_apply.apply(action="rollback", target_tag=None)
    assert result["target_tag"] == "v0.9.0"
    assert result["action"] == "rollback"


def test_rollback_without_target_raises():
    """Rolling back when no previous version was recorded → 409."""
    from app.middleware.errors import AppError
    with pytest.raises(AppError) as exc:
        release_apply.apply(action="rollback", target_tag=None)
    assert exc.value.status_code == 409
    assert exc.value.code == "NO_ROLLBACK_TARGET"


def test_get_version_reports_state(monkeypatch):
    """get_version() folds FH_TAG env + rollback_target file + job state
    into the shape the SPA expects."""
    monkeypatch.setenv("FH_TAG", "v1.2.3")
    release_apply.ROLLBACK_FILE.write_text(json.dumps({"tag": "v1.2.2"}))

    info = release_apply.get_version()
    assert info["current_tag"] == "v1.2.3"
    assert info["rollback_target"] == "v1.2.2"
    assert info["job_in_progress"] is None

    # Now mark a job in-flight; should surface its id.
    result = release_apply.apply(action="update", target_tag="v1.2.4")
    info2 = release_apply.get_version()
    assert info2["job_in_progress"] == result["job_id"]


def test_get_job_normalizes_pending_to_queued():
    """The state file's `pending` status surfaces as `queued` to the SPA."""
    result = release_apply.apply(action="update", target_tag="v1.0.1")
    job = release_apply.get_job(result["job_id"])
    assert job["state"] == "queued"
    assert job["id"] == result["job_id"]


def test_get_job_unknown_raises_404():
    from app.middleware.errors import AppError
    with pytest.raises(AppError) as exc:
        release_apply.get_job("nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_endpoint_requires_password(client, db, make_user, login_as):
    admin = make_user(email="adm@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    # httpx 0.28+ deprecates per-request cookies=; the AsyncClient jar
    # already carries the fh_refresh set by login_as above, so the kwarg
    # was redundant. Same pattern in every test in this file.
    _ = cookies
    r = await client.post(
        "/api/admin/system/update",
        json={"password": "wrong-pw", "target_tag": "v1.0.1"},
        headers=headers,
    )
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_update_endpoint_writes_audit_and_dispatches(
    client, db, make_user, login_as
):
    admin = make_user(email="adm2@test.local", role=UserRole.admin)
    other_admin = make_user(email="adm3@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm2@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    _ = cookies
    r = await client.post(
        "/api/admin/system/update",
        json={"password": "TestPassword123!", "target_tag": "v1.0.1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Audit row exists with right type + actor + target.
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.update_triggered.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_user_id == admin.id
    assert row.target_id == job_id
    assert row.extra and row.extra.get("target_tag") == "v1.0.1"

    # ops_alert fan-out wrote a notification row for the OTHER admin.
    from app.models.notification import Notification, NotificationCategory
    notifs = (
        db.query(Notification)
        .filter(
            Notification.user_id == other_admin.id,
            Notification.category == NotificationCategory.ops_alert,
        )
        .all()
    )
    assert len(notifs) >= 1


@pytest.mark.asyncio
async def test_rollback_endpoint_uses_target_file(
    client, db, make_user, login_as
):
    admin = make_user(email="adm4@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm4@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    _ = cookies
    # No rollback target file → 409 NO_ROLLBACK_TARGET.
    r = await client.post(
        "/api/admin/system/rollback",
        json={"password": "TestPassword123!"},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "NO_ROLLBACK_TARGET"

    # Write the file, retry.
    release_apply.ROLLBACK_FILE.write_text(json.dumps({"tag": "v0.9.0"}))
    r = await client.post(
        "/api/admin/system/rollback",
        json={"password": "TestPassword123!"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_tag"] == "v0.9.0"

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.rollback_triggered.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_user_id == admin.id
