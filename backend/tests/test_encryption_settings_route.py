"""The encryption-at-rest switch: GET/PUT /api/admin/settings/encryption.

Turning it ON commits file recovery to this instance's .env, so it needs an
explicit key-custody acknowledgement AND the caller's password. Turning it OFF
needs the password too - it decides whether new uploads leave plaintext on the
volume. Neither is a registry tunable: /settings/advanced has no step-up.
"""
from __future__ import annotations

import json

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import file_encryption as fe
from app.services import settings as settings_svc
from app.services import settings_registry
from tests._encryption_helpers import encrypt_row, store_plain

K = settings_svc.Keys
PASSWORD = "TestPassword123!"
URL = "/api/admin/settings/encryption"


def _audit(db, event: AuditEventType) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.event_type == event.value).all()


async def _headers(make_user, login_as, email="enc-admin@test.local", role=UserRole.admin):
    make_user(email=email, role=role)
    token, _cookies = await login_as(email, PASSWORD)
    return {"Authorization": f"Bearer {token}"}


def _set_enabled(db, on: bool) -> None:
    settings_svc.set_value(db, key=K.STORAGE_ENCRYPT_AT_REST, value="true" if on else "false", actor=None)
    db.commit()


def test_it_ships_off(db):
    assert fe.is_enabled(db) is False


def test_the_switch_is_not_a_registry_tunable():
    assert K.STORAGE_ENCRYPT_AT_REST not in settings_registry.BY_KEY
    assert K.STORAGE_ENCRYPT_LAST_RUN not in settings_registry.BY_KEY


def test_config_backup_keeps_the_switch_but_not_the_run_history():
    from app.services import config_backup

    assert K.STORAGE_ENCRYPT_LAST_RUN in config_backup._TRANSIENT_SETTING_KEYS
    assert K.STORAGE_ENCRYPT_AT_REST not in config_backup._TRANSIENT_SETTING_KEYS


@pytest.mark.asyncio
async def test_the_status_counts(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    u = make_user(email="enc-owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    rows = []
    for i, state in enumerate([FileState.clean, FileState.clean, FileState.infected, FileState.deleted]):
        data = bytes([i]) * (100 + i)
        f = File(share_id=sh.id, original_filename=f"{i}.bin", size_bytes=len(data), uploaded_by_id=u.id,
                 state=state, storage_path=store_plain(data, name=f"s{i}"))
        db.add(f)
        rows.append(f)
    waiting = File(share_id=sh.id, original_filename="w.bin", size_bytes=1, uploaded_by_id=u.id,
                   state=FileState.ready_unscanned, storage_path=store_plain(b"w", name="w"),
                   release_verdict="clean")
    db.add(waiting)
    db.commit()
    encrypt_row(db, rows[0])

    r = await client.get(URL, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["backend"] == "local"
    # one clean encrypted; one clean + one infected plaintext; deleted not counted
    assert body["files"] == {"encrypted": 1, "plaintext": 2, "plaintext_bytes": 101 + 102,
                             "awaiting_encryption": 1}
    assert body["inbound_attachments"] == {"encrypted": 0, "plaintext": 0}
    assert body["last_run"] is None
    assert (body["deferred"], body["backfill_task_enabled"]) == (0, True)


@pytest.mark.asyncio
async def test_turning_it_on_needs_the_acknowledgement_first(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.put(URL, json={"enabled": True, "password": PASSWORD}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "ENCRYPTION_ACK_REQUIRED"
    assert fe.is_enabled(db) is False
    assert not _audit(db, AuditEventType.step_up_failed), "no password check spent on a refused request"


@pytest.mark.asyncio
@pytest.mark.parametrize("password", [None, "wrong-password"])
@pytest.mark.parametrize("turn_on", [True, False])
async def test_either_direction_needs_the_password(client, db, make_user, login_as, password, turn_on):
    h = await _headers(make_user, login_as, f"enc-pw-{password}-{turn_on}@test.local")
    _set_enabled(db, not turn_on)
    body: dict = {"enabled": turn_on, "acknowledge_key_custody": True}
    if password:
        body["password"] = password
    r = await client.put(URL, json=body, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INVALID_PASSWORD"
    assert fe.is_enabled(db) is (not turn_on)
    assert _audit(db, AuditEventType.step_up_failed)
    assert not _audit(db, AuditEventType.encryption_at_rest_changed)


@pytest.mark.asyncio
async def test_turning_it_on_and_off_with_the_password(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.put(URL, json={"enabled": True, "acknowledge_key_custody": True, "password": PASSWORD},
                         headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and fe.is_enabled(db) is True
    (row,) = _audit(db, AuditEventType.encryption_at_rest_changed)
    assert (row.target_type, row.target_id, row.extra) == ("settings", "encryption", {"enabled": True})
    assert PASSWORD not in json.dumps(row.extra)

    r = await client.put(URL, json={"enabled": False, "password": PASSWORD}, headers=h)
    assert r.status_code == 200, r.text
    assert fe.is_enabled(db) is False
    assert [a.extra["enabled"] for a in _audit(db, AuditEventType.encryption_at_rest_changed)] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [True, False])
async def test_a_no_op_needs_nothing_and_records_nothing(client, db, make_user, login_as, on):
    h = await _headers(make_user, login_as, f"enc-noop-{on}@test.local")
    _set_enabled(db, on)
    r = await client.put(URL, json={"enabled": on}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is on
    assert not _audit(db, AuditEventType.encryption_at_rest_changed)


@pytest.mark.asyncio
async def test_the_advanced_route_cannot_turn_it_on(client, db, make_user, login_as):
    h = await _headers(make_user, login_as)
    r = await client.put("/api/admin/settings/advanced",
                         json={"updates": {K.STORAGE_ENCRYPT_AT_REST: True}}, headers=h)
    assert r.status_code == 400
    assert fe.is_enabled(db) is False


@pytest.mark.asyncio
async def test_non_admins_cannot_touch_it(client, db, make_user, login_as):
    h = await _headers(make_user, login_as, "enc-emp@test.local", role=UserRole.employee)
    assert (await client.get(URL, headers=h)).status_code == 403
    r = await client.put(URL, json={"enabled": True, "acknowledge_key_custody": True, "password": PASSWORD},
                         headers=h)
    assert r.status_code == 403
    assert fe.is_enabled(db) is False


@pytest.mark.asyncio
async def test_the_last_run_is_read_field_by_field(client, db, make_user, login_as):
    """The summary is this instance's own history; one in another shape must
    not 500 the page."""
    h = await _headers(make_user, login_as)

    def _put(value: str) -> None:
        settings_svc.set_value(db, key=K.STORAGE_ENCRYPT_LAST_RUN, value=value, actor=None)
        db.commit()

    _put("{not json")
    assert (await client.get(URL, headers=h)).json()["last_run"] is None
    _put(json.dumps({"encrypted": 3}))
    assert (await client.get(URL, headers=h)).json()["last_run"] is None, "no finished_at"
    _put(json.dumps({"finished_at": "2026-10-02T10:00:00", "encrypted": 3, "failed": "x", "stopped": 7}))
    assert (await client.get(URL, headers=h)).json()["last_run"] == {
        "finished_at": "2026-10-02T10:00:00", "encrypted": 3, "failed": 0, "deferred": 0, "skipped": 0,
        "remaining": 0, "stopped": None,
    }


@pytest.fixture
def enqueued(monkeypatch):
    from app.services import job_queue

    jobs: list[str] = []
    real = job_queue.enqueue  # conftest's no-op: sign-in mails etc. still go there

    def _record(name, *a, **kw):
        if name.startswith("encrypt_"):
            jobs.append(name)
        else:
            real(name, *a, **kw)

    monkeypatch.setattr(job_queue, "enqueue", _record)
    return jobs


@pytest.mark.asyncio
async def test_turning_it_on_kicks_the_backfill_and_off_does_not(client, db, make_user, login_as, enqueued):
    h = await _headers(make_user, login_as)
    await client.put(URL, json={"enabled": True, "acknowledge_key_custody": True, "password": PASSWORD},
                     headers=h)
    assert enqueued == ["encrypt_existing_files"]
    await client.put(URL, json={"enabled": False, "password": PASSWORD}, headers=h)
    assert enqueued == ["encrypt_existing_files"]


@pytest.mark.asyncio
async def test_retry_failed_clears_the_deferrals_and_kicks_the_backfill(client, db, make_user, login_as,
                                                                       enqueued):
    from app.services import encryption_lanes as lanes

    h = await _headers(make_user, login_as)
    for _ in range(3):
        lanes._record_failure("file:abc")
    assert (await client.get(URL, headers=h)).json()["deferred"] == 1
    r = await client.post(f"{URL}/retry-failed", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["deferred"] == 0
    assert enqueued == ["encrypt_existing_files"]


@pytest.mark.asyncio
async def test_retry_failed_says_so_when_redis_is_down(client, db, make_user, login_as, enqueued, monkeypatch):
    from app.services import encryption_lanes as lanes

    h = await _headers(make_user, login_as)

    def _down():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(lanes, "get_redis", _down)
    r = await client.post(f"{URL}/retry-failed", headers=h)
    assert r.status_code == 503 and r.json()["code"] == "REDIS_UNAVAILABLE"
    assert enqueued == []
    assert (await client.get(URL, headers=h)).json()["deferred"] is None


@pytest.mark.asyncio
async def test_retry_failed_is_admin_only(client, make_user, login_as):
    h = await _headers(make_user, login_as, "enc-retry-emp@test.local", role=UserRole.employee)
    assert (await client.post(f"{URL}/retry-failed", headers=h)).status_code == 403
