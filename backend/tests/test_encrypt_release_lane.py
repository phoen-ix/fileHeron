"""The release lane of encryption at rest (services/encryption_lanes).

With the switch on, a scanned file is HELD at `ready_unscanned` with its
verdict recorded, and the lane flips it to `clean` and onto the ciphertext in
one conditional UPDATE - so it is never downloadable as plaintext, and the
recipients are mailed only after the swap. Any failure releases it as
plaintext with an audit row (`ready_unscanned` stays terminal); a worker
shutdown leaves the verdict for the next run.
"""
from __future__ import annotations

import os
import threading

import pytest

from app.config import settings
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.storage_purge import StoragePurge
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.services import encryption_lanes as lanes
from app.services import file_encryption as fe
from app.services import settings as settings_svc
from app.services import share as share_svc
from app.services import storage_backend as sb
from app.services.av_scan import ScanResult
from app.utils import file_crypto
from app.utils.timeutil import utc_now
from app.workers.av_scan import av_scan_file
from tests._encryption_helpers import store_plain

DATA = os.urandom(file_crypto.CHUNK_SIZE + 4321)


def _enable(db, on: bool = True) -> None:
    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST,
                           value="true" if on else "false", actor=None)
    db.commit()


@pytest.fixture
def share(db, make_user):
    u = make_user(email="lane@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.commit()
    return sh


@pytest.fixture
def new_file(db, share):
    def _make(data: bytes = DATA, name: str = "up.bin") -> File:
        f = File(share_id=share.id, original_filename=name, size_bytes=len(data),
                 uploaded_by_id=share.created_by_id, state=FileState.ready_unscanned,
                 storage_path=store_plain(data, name=name), finalized_at=utc_now())
        db.add(f)
        db.commit()
        return f

    return _make


@pytest.fixture
def clamd_clean(monkeypatch):
    calls = []

    def _scan(path):
        calls.append(path)
        return ScanResult(state="clean", signature=None, raw="OK")

    monkeypatch.setattr(av_scan_svc, "scan_path", _scan)
    monkeypatch.setattr(settings, "AV_SKIP", False)
    return calls


@pytest.fixture
def kicks(monkeypatch):
    calls = []

    async def _kick():
        calls.append(1)

    monkeypatch.setattr(lanes, "kick_release_lane", _kick)
    return calls


@pytest.fixture
def notices(db, monkeypatch):
    """Record each recipient notice with the state the file was in AT THAT
    MOMENT - the property is that nobody is told before the swap."""
    seen = []

    def _notify(_db, share_id):
        rows = db.query(File.state, File.enc_version).filter(File.share_id == share_id).all()
        seen.append((share_id, rows))
        return False

    monkeypatch.setattr(share_svc, "notify_if_downloadable", _notify)
    return seen


def _read(row: File) -> bytes:
    with fe.open_plaintext(sb.get_storage_backend(), row.storage_path, fe.cipher_for_file(row)) as fh:
        return fh.read()


def _audit(db, event):
    return db.query(AuditLog).filter(AuditLog.event_type == event.value).all()


@pytest.mark.asyncio
async def test_with_encryption_off_the_scan_releases_as_before(db, new_file, clamd_clean, kicks, notices):
    f = new_file()
    out = await av_scan_file({}, f.id)
    db.refresh(f)
    assert out["state"] == "clean"
    assert (f.state, f.enc_version, f.release_verdict) == (FileState.clean, None, None)
    assert kicks == [] and len(notices) == 1


@pytest.mark.asyncio
async def test_a_clean_verdict_is_held_then_released_encrypted(db, share, new_file, clamd_clean, kicks,
                                                               notices):
    _enable(db)
    f = new_file()
    plain = f.storage_path
    out = await av_scan_file({}, f.id)
    db.refresh(f)
    assert out["state"] == "awaiting_encryption"
    assert (f.state, f.release_verdict, f.enc_version) == (FileState.ready_unscanned, "clean", None)
    assert kicks == [1]
    assert notices == [], "nobody is told about a file that is not downloadable yet"

    assert lanes.run_release_lane(db) == {"encrypted": 1}
    db.refresh(f)
    assert (f.state, f.release_verdict, f.av_unscanned) == (FileState.clean, None, False)
    assert f.enc_version == file_crypto.FORMAT_VERSION and f.storage_path.endswith(".fhe")
    assert _read(f) == DATA
    assert not sb.get_storage_backend().exists(plain), "the plaintext goes at once"
    assert db.query(StoragePurge).count() == 0
    assert notices == [(share.id, [(FileState.clean, file_crypto.FORMAT_VERSION)])]


@pytest.mark.asyncio
async def test_an_unscanned_verdict_is_audited_once_at_release(db, new_file, clamd_clean, kicks, notices,
                                                              monkeypatch):
    monkeypatch.setattr(settings, "AV_MAX_SCAN_BYTES", 10)
    _enable(db)
    f = new_file()
    out = await av_scan_file({}, f.id)
    assert out == {"file_id": f.id, "state": "awaiting_encryption", "av_unscanned": True}
    db.refresh(f)
    assert f.release_verdict == "unscanned:exceeds_av_max_scan_bytes"
    assert _audit(db, AuditEventType.file_served_unscanned) == [], "not served yet"

    lanes.run_release_lane(db)
    db.refresh(f)
    assert (f.state, f.av_unscanned, f.enc_version) == (FileState.clean, True, 1)
    (row,) = _audit(db, AuditEventType.file_served_unscanned)
    assert row.extra["reason"] == "exceeds_av_max_scan_bytes"


@pytest.mark.asyncio
async def test_a_held_file_is_never_rescanned_but_re_kicks_the_lane(db, new_file, clamd_clean, kicks):
    _enable(db)
    f = new_file()
    await av_scan_file({}, f.id)
    clamd_clean.clear()
    out = await av_scan_file({}, f.id)  # the stale sweep re-enqueued it
    assert out == {"file_id": f.id, "state": "awaiting_encryption"}
    assert clamd_clean == [] and kicks == [1, 1]


def _hold(db, f: File, verdict: str = "clean") -> None:
    assert lanes.hold_for_encryption(db, f, verdict)
    db.commit()


def test_no_space_releases_as_plaintext_with_an_audit_row(db, new_file, notices, monkeypatch):
    from app.services import storage

    _enable(db)
    f = new_file()
    plain = f.storage_path
    _hold(db, f)
    monkeypatch.setattr(storage, "get_disk_stats", lambda _p: {"free_bytes": 100, "total_bytes": 10**9,
                                                               "used_bytes": 0, "percent_free": 0.1})
    assert lanes.run_release_lane(db) == {"plaintext": 1}
    db.refresh(f)
    assert (f.state, f.enc_version, f.release_verdict, f.storage_path) == (FileState.clean, None, None, plain)
    (row,) = _audit(db, AuditEventType.file_encryption_deferred)
    assert (row.target_id, row.extra) == (f.id, {"reason": "insufficient_space", "lane": "release"})
    assert len(notices) == 1


def test_a_failed_write_releases_plaintext_and_leaves_no_debris(db, new_file, notices, monkeypatch):
    _enable(db)
    f = new_file()
    _hold(db, f)
    backend = sb.get_storage_backend()
    written = []

    def _broken(loc, reader):
        written.append(loc)
        with open(loc, "wb") as fh:
            fh.write(reader.read(100))
        raise OSError("device error")

    monkeypatch.setattr(backend, "write_stream", _broken)
    assert lanes.run_release_lane(db) == {"plaintext": 1}
    db.refresh(f)
    assert (f.state, f.enc_version) == (FileState.clean, None)
    assert _read(f) == DATA
    assert _audit(db, AuditEventType.file_encryption_deferred)[0].extra["reason"] == "OSError"
    assert not os.path.exists(written[0]) and db.query(StoragePurge).count() == 0


def test_the_switch_turned_off_meanwhile_releases_without_an_audit_row(db, new_file, notices):
    _enable(db)
    f = new_file()
    _hold(db, f)
    _enable(db, False)
    assert lanes.run_release_lane(db) == {"released": 1}
    db.refresh(f)
    assert (f.state, f.enc_version, f.release_verdict) == (FileState.clean, None, None)
    assert _audit(db, AuditEventType.file_encryption_deferred) == []
    assert len(notices) == 1


def test_a_file_deleted_while_held_is_never_resurrected(db, new_file, notices):
    _enable(db)
    f = new_file()
    _hold(db, f)
    f.state = FileState.deleted
    db.commit()
    assert lanes.run_release_lane(db) == {}
    db.refresh(f)
    assert f.state == FileState.deleted and notices == []


def test_expiry_between_write_and_swap_wins(db, new_file, notices, monkeypatch):
    _enable(db)
    f = new_file()
    _hold(db, f)
    backend = sb.get_storage_backend()
    real = backend.write_stream
    written = []

    def _write_then_expire(loc, reader):
        real(loc, reader)
        written.append(loc)
        db.query(File).filter(File.id == f.id).update({"state": FileState.deleted})
        db.commit()

    monkeypatch.setattr(backend, "write_stream", _write_then_expire)
    assert lanes.run_release_lane(db) == {"superseded": 1}
    db.refresh(f)
    assert (f.state, f.enc_version) == (FileState.deleted, None)
    assert not backend.exists(written[0]), "our ciphertext was debris and is purged"
    assert notices == []


def test_a_cancel_leaves_the_verdict_for_the_next_run(db, new_file):
    _enable(db)
    f = new_file()
    _hold(db, f)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(file_crypto.EncryptionCancelledError):
        lanes.run_release_lane(db, cancel=cancel)
    db.refresh(f)
    assert (f.state, f.release_verdict) == (FileState.ready_unscanned, "clean")
    assert lanes.run_release_lane(db) == {"encrypted": 1}


def test_a_cancel_mid_file_is_not_a_failure(db, new_file, monkeypatch):
    _enable(db)
    f = new_file()
    _hold(db, f)
    cancel = threading.Event()
    real = file_crypto.EncryptingReader.read

    def _read_then_cancel(self, n=-1):
        cancel.set()
        return real(self, n)

    monkeypatch.setattr(file_crypto.EncryptingReader, "read", _read_then_cancel)
    with pytest.raises(file_crypto.EncryptionCancelledError):
        lanes.release_one(db, f.id, cancel=cancel)
    db.refresh(f)
    assert (f.state, f.release_verdict) == (FileState.ready_unscanned, "clean")
    assert _audit(db, AuditEventType.file_encryption_deferred) == []


def test_files_go_oldest_first(db, new_file, monkeypatch):
    _enable(db)
    a, b = new_file(b"a" * 10, "a.bin"), new_file(b"b" * 10, "b.bin")
    for row in (b, a):
        _hold(db, row)
    order = []
    real = lanes.release_one
    monkeypatch.setattr(lanes, "release_one", lambda d, fid, **kw: order.append(fid) or real(d, fid, **kw))
    lanes.run_release_lane(db)
    assert order == [a.id, b.id]


def test_a_busy_lane_is_marked_and_the_holder_takes_the_new_file(db, new_file, monkeypatch,
                                                                 _isolate_encryption_lanes):
    """A verdict that lands while the lane runs must not be left behind."""
    _enable(db)
    first, second = new_file(b"1" * 10, "1.bin"), new_file(b"2" * 10, "2.bin")
    _hold(db, first)
    real = lanes.release_one
    nested = []

    def _and_meanwhile(d, fid, **kw):
        out = real(d, fid, **kw)
        if fid == first.id:
            _hold(db, second)
            nested.append(lanes.run_release_lane(db))  # the second verdict's own kick
        return out

    monkeypatch.setattr(lanes, "release_one", _and_meanwhile)
    assert lanes.run_release_lane(db) == {"encrypted": 2}
    assert nested == [{"busy": 1}]
    db.refresh(second)
    assert (second.state, second.enc_version) == (FileState.clean, 1)
    assert _isolate_encryption_lanes == {}, "lock released, nothing left marked"


def test_a_mark_after_the_last_pass_is_picked_up_after_the_lock_goes(db, new_file, monkeypatch):
    _enable(db)
    f = new_file(b"x" * 10, "x.bin")
    real_release = lanes.RedisLease.release
    state = {"done": False}

    def _release_then_a_verdict_lands(self):
        real_release(self)
        if not state["done"]:
            state["done"] = True
            _hold(db, f)
            lanes._mark_dirty()

    monkeypatch.setattr(lanes.RedisLease, "release", _release_then_a_verdict_lands)
    assert lanes.run_release_lane(db) == {"encrypted": 1}


def test_redis_down_runs_unguarded(db, new_file, monkeypatch):
    _enable(db)
    f = new_file()
    _hold(db, f)

    def _down():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(lanes, "get_redis", _down)
    assert lanes.run_release_lane(db) == {"encrypted": 1}


def test_hold_refuses_an_unknown_verdict(db, new_file):
    f = new_file()
    with pytest.raises(ValueError):
        lanes.hold_for_encryption(db, f, "infected")


def test_hold_is_conditional(db, new_file):
    f = new_file()
    _hold(db, f)
    assert lanes.hold_for_encryption(db, f, "clean") is False, "already held"
    f.state = FileState.deleted
    f.release_verdict = None
    db.commit()
    assert lanes.hold_for_encryption(db, f, "clean") is False


def test_the_job_is_registered_with_its_own_limits():
    from app.workers import worker

    (fn,) = [f for f in worker.WorkerSettings.functions if getattr(f, "name", None) == lanes.RELEASE_JOB]
    assert (fn.timeout_s, fn.max_tries, fn.keep_result_s) == (6 * 3600, 1, 0)


def test_a_mark_that_cannot_be_cleared_does_not_spin_the_lane(db, new_file, monkeypatch):
    _enable(db)
    f = new_file()
    _hold(db, f)
    monkeypatch.setattr(lanes, "_take_dirty", lambda: False)
    monkeypatch.setattr(lanes, "_is_dirty", lambda: True)
    out = lanes.run_release_lane(db)
    assert out == {"encrypted": 1, "passes_exhausted": 1}
