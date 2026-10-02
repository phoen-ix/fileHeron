"""The way back out of encryption at rest.

Rollback refuses to go to a release that cannot read encrypted files while any
exist; scripts/decrypt_files_at_rest.py turns every stored object back into
plaintext so it can.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass
from app.models.share import Share, ShareKind, ShareState
from app.models.storage_purge import PURGE_ENC_PLAINTEXT, StoragePurge
from app.models.user import UserRole
from app.services import encryption_lanes as lanes
from app.services import file_encryption as fe
from app.services import release_apply
from app.services import settings as settings_svc
from app.services import storage_backend as sb
from app.utils import file_crypto
from app.utils.timeutil import utc_now
from tests._encryption_helpers import encrypt_row, store_plain

PASSWORD = "TestPassword123!"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "decrypt_files_at_rest.py"
DATA = os.urandom(file_crypto.CHUNK_SIZE + 10)


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(release_apply, "STATE_DIR", state)
    monkeypatch.setattr(release_apply, "STATE_FILE", state / "current_job.json")
    monkeypatch.setattr(release_apply, "ROLLBACK_FILE", state / "rollback_target.json")


@pytest.fixture
def stored(db, make_user):
    u = make_user(email="ops@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()

    def _make(*, encrypted: bool = True, state: FileState = FileState.clean) -> File:
        f = File(share_id=sh.id, original_filename="o.bin", size_bytes=len(DATA), uploaded_by_id=u.id,
                 state=state, storage_path=store_plain(DATA, name="ops"))
        db.add(f)
        db.commit()
        if encrypted:
            encrypt_row(db, f)
        return f

    return _make


def _target(head: str | None) -> None:
    rec: dict = {"tag": "v2.24.0"}
    if head is not None:
        rec["alembic_head"] = head
    release_apply.ROLLBACK_FILE.write_text(json.dumps(rec))


# --- the rollback guard -------------------------------------------------------------


def test_the_guard_names_the_real_migration():
    versions = Path(release_apply._ALEMBIC_DIR) / "versions"
    assert (versions / f"{release_apply.ENCRYPTION_REVISION}_encryption_at_rest.py").is_file()


@pytest.mark.parametrize("head", ["202610020001", None, "999999999999"])
def test_a_target_that_cannot_read_them_is_refused(db, stored, head):
    stored()
    _target(head)
    with pytest.raises(Exception) as exc:
        release_apply.assert_rollback_can_read_files(db)
    assert getattr(exc.value, "code", None) == "ROLLBACK_BLOCKED_BY_ENCRYPTION"
    assert exc.value.details == {"encrypted": 1, "target_tag": "v2.24.0"}


def test_a_target_that_can_read_them_passes(db, stored):
    stored()
    _target(release_apply.ENCRYPTION_REVISION)
    release_apply.assert_rollback_can_read_files(db)


def test_nothing_encrypted_passes_whatever_the_target(db, stored):
    stored(encrypted=False)
    _target("202610020001")
    release_apply.assert_rollback_can_read_files(db)


def test_an_encrypted_row_without_bytes_does_not_count(db, stored):
    f = stored()
    f.state, f.storage_path = FileState.deleted, None
    db.commit()
    _target("202610020001")
    release_apply.assert_rollback_can_read_files(db)


@pytest.mark.asyncio
async def test_the_rollback_route_refuses_before_writing_a_job(client, db, make_user, login_as, stored):
    make_user(email="ops-admin@test.local", role=UserRole.admin)
    token, _ = await login_as("ops-admin@test.local", PASSWORD)
    stored()
    _target("202610020001")
    r = await client.post("/api/admin/system/rollback", json={"password": PASSWORD},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "ROLLBACK_BLOCKED_BY_ENCRYPTION"
    assert not release_apply.STATE_FILE.exists()
    assert not db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.rollback_triggered.value).count()


# --- the decrypt script -------------------------------------------------------------


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["decrypt_files_at_rest.py", *argv])
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _enable(db, on=True):
    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST,
                           value="true" if on else "false", actor=None)
    db.commit()


def _attachment(db) -> InboundAttachment:
    msg = InboundMessage(received_at=utc_now(), sender_email="a@b.c", subject="s", imap_uid=3, uidvalidity=1,
                         classification=MessageClass.normal, has_attachments=True)
    db.add(msg)
    db.flush()
    a = InboundAttachment(message_id=msg.id, filename="a.pdf", size_bytes=len(DATA),
                          storage_key=store_plain(DATA, name="opsatt"), av_state=AttachmentAVState.clean)
    db.add(a)
    db.commit()
    encrypt_row(db, a, kind="inbound_attachment", locator_attr="storage_key")
    return a


def test_it_refuses_while_encryption_is_on(monkeypatch, db, stored, capsys):
    stored()
    _enable(db)
    assert _run(monkeypatch) == 2
    assert "--turn-off" in capsys.readouterr().err
    assert fe.count_encrypted(db) == 1


def test_it_decrypts_everything_and_rollback_is_then_allowed(monkeypatch, db, stored):
    f, quarantined = stored(), stored(state=FileState.infected)
    att = _attachment(db)
    enc = f.storage_path
    assert _run(monkeypatch) == 0
    for row in (f, quarantined):
        db.refresh(row)
        assert row.enc_version is None and row.key_encrypted is None
        with sb.get_storage_backend().open(row.storage_path) as fh:
            assert fh.read() == DATA
    db.refresh(att)
    assert att.enc_version is None
    assert sb.get_storage_backend().exists(enc), "the ciphertext waits out the grace by default"
    _target("202610020001")
    release_apply.assert_rollback_can_read_files(db)


def test_turn_off_switches_it_off_with_an_audit_row(monkeypatch, db, stored):
    stored()
    _enable(db)
    assert _run(monkeypatch, "--turn-off") == 0
    assert fe.is_enabled(db) is False
    row = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.encryption_at_rest_changed.value).one()
    assert row.extra == {"enabled": False, "via": "decrypt_script"}
    assert fe.count_encrypted(db) == 0


def test_purge_now_leaves_no_replaced_copy_behind(monkeypatch, db, stored):
    """Before a rollback: an old release never deletes them."""
    f = stored()
    enc = f.storage_path
    leftover = store_plain(b"an earlier backfill's plaintext", name="left")
    db.add(StoragePurge(locator=leftover, reason=PURGE_ENC_PLAINTEXT, ref="x",
                        not_before=utc_now() + timedelta(minutes=40)))
    db.commit()
    assert _run(monkeypatch, "--purge-now") == 0
    backend = sb.get_storage_backend()
    assert not backend.exists(enc) and not backend.exists(leftover)
    assert db.query(StoragePurge).count() == 0


def test_dry_run_changes_nothing(monkeypatch, db, stored, capsys):
    stored()
    assert _run(monkeypatch, "--dry-run") == 0
    assert "1 stored object(s) are encrypted" in capsys.readouterr().out
    assert fe.count_encrypted(db) == 1


def test_it_waits_for_a_running_backfill(monkeypatch, db, stored):
    stored()
    lease = lanes.RedisLease(lanes._BACKFILL_LOCK)
    assert lease.acquire()
    try:
        assert _run(monkeypatch) == 3
    finally:
        lease.release()
    assert fe.count_encrypted(db) == 1


def test_a_failure_is_reported_and_exits_nonzero(monkeypatch, db, stored, capsys):
    stored()
    backend = sb.get_storage_backend()

    def _broken(_loc, _reader):
        raise OSError("device error")

    monkeypatch.setattr(backend, "write_stream", _broken)
    assert _run(monkeypatch) == 1
    assert "OSError" in capsys.readouterr().err
    assert fe.count_encrypted(db) == 1


def test_the_backfill_stops_when_switched_off_mid_run(db, stored, monkeypatch):
    """The decrypt script must not race a backfill that started while it was on."""
    _enable(db)
    for _ in range(3):
        stored(encrypted=False)
    calls = {"n": 0}
    real = fe.is_enabled

    def _off_after_the_first_file(d):
        calls["n"] += 1  # 1: the run's own check, 2: before file 1, 3: before file 2
        return real(d) if calls["n"] <= 2 else False

    monkeypatch.setattr(fe, "is_enabled", _off_after_the_first_file)
    out = lanes.run_backfill(db)
    assert (out["stopped"], out["encrypted"]) == ("disabled", 1)


def test_the_syspath_shim_survives_a_foreign_cwd():
    import subprocess

    # S603: this interpreter and a path literal from this file; no shell.
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, cwd="/", timeout=60,
    )
    combined = r.stdout + r.stderr
    assert "Traceback" not in combined, combined
    assert r.returncode == 0 and "--turn-off" in combined


# --- erasure ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erasure_deletes_a_swapped_files_waiting_copy_at_once(db, make_user):
    """"Erased" must be true when the receipt says so, not an hour later."""
    from app.services import erasure

    admin = make_user(email="ops-eraser@test.local", role=UserRole.admin)
    subject = make_user(email="ops-subject@test.local", role=UserRole.employee)
    sh = Share(created_by_id=subject.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(share_id=sh.id, original_filename="cv.pdf", size_bytes=len(DATA), uploaded_by_id=subject.id,
             state=FileState.clean, storage_path=store_plain(DATA, name="cv"))
    db.add(f)
    db.commit()
    plain = f.storage_path
    assert fe.rewrite_stored(db, fe.target_for_file(f), encrypt=True, purge_after=timedelta(hours=1))
    assert sb.get_storage_backend().exists(plain), "precondition: the plaintext copy waits"

    erasure.erase_user(db, target=subject, actor=admin)
    assert not sb.get_storage_backend().exists(plain)
    assert db.query(StoragePurge).count() == 0
