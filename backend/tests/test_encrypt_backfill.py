"""The encryption backfill (cron `encrypt_existing_files`) and encryption of
inbound mail attachments at ingest.

The backfill encrypts what is already stored, one object at a time within a
budget; it leaves recently served files alone, defers an object after three
failures, stops on low space, and reports a run that got nothing done as a
FAILED cron run. Every run also sweeps due purges and releases files the
release lane left held - the backstop for a kick that never arrived.
"""
from __future__ import annotations

import os
import threading
from datetime import timedelta

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
from app.services import settings as settings_svc
from app.services import storage_backend as sb
from app.services import transfer_activity
from app.services.cron_tracker import CRON_FAILED_KEY
from app.utils import file_crypto
from app.utils.timeutil import utc_now
from tests._encryption_helpers import store_plain

DATA = os.urandom(file_crypto.CHUNK_SIZE + 77)


def _enable(db, on: bool = True) -> None:
    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST,
                           value="true" if on else "false", actor=None)
    db.commit()


@pytest.fixture
def share(db, make_user):
    u = make_user(email="bf@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.commit()
    return sh


@pytest.fixture
def stored(db, share):
    def _make(state=FileState.clean, data: bytes = DATA, name: str = "f") -> File:
        f = File(share_id=share.id, original_filename=f"{name}.bin", size_bytes=len(data),
                 uploaded_by_id=share.created_by_id, state=state,
                 storage_path=store_plain(data, name=name), finalized_at=utc_now())
        db.add(f)
        db.commit()
        return f

    return _make


@pytest.fixture
def attachment(db):
    def _make(data: bytes = DATA) -> InboundAttachment:
        msg = InboundMessage(received_at=utc_now(), sender_email="a@b.c", subject="s",
                             imap_uid=int.from_bytes(os.urandom(3), "big"), uidvalidity=1,
                             classification=MessageClass.normal, has_attachments=True)
        db.add(msg)
        db.flush()
        a = InboundAttachment(message_id=msg.id, filename="x.pdf", size_bytes=len(data),
                              storage_key=store_plain(data, name="att"), av_state=AttachmentAVState.clean)
        db.add(a)
        db.commit()
        return a

    return _make


def _read(row: File) -> bytes:
    with fe.open_plaintext(sb.get_storage_backend(), row.storage_path, fe.cipher_for_file(row)) as fh:
        return fh.read()


def _audit(db, event):
    return db.query(AuditLog).filter(AuditLog.event_type == event.value).all()


def _last_run(db):
    return fe.status(db)["last_run"]


def test_switched_off_it_encrypts_nothing_but_still_sweeps_and_releases(db, stored):
    f = stored()
    old = stored(name="gone")
    db.add(StoragePurge(locator=old.storage_path, reason=PURGE_ENC_PLAINTEXT, ref=old.id,
                        not_before=utc_now() - timedelta(seconds=1)))
    held = stored(state=FileState.ready_unscanned, name="held")
    held.release_verdict = "clean"
    db.commit()

    out = lanes.run_backfill(db)
    assert out["skipped"] == "disabled" and out["purged"] == 1
    assert out["held_released"] == {"released": 1}
    db.refresh(f)
    db.refresh(held)
    assert f.enc_version is None
    assert (held.state, held.release_verdict) == (FileState.clean, None)


def test_it_encrypts_clean_and_quarantined_files_and_attachments(db, stored, attachment):
    _enable(db)
    clean, infected = stored(), stored(state=FileState.infected, name="inf")
    untouched = [stored(state=s, name=s.value) for s in (FileState.deleted, FileState.ready_unscanned,
                                                        FileState.uploading)]
    att = attachment()
    plain = clean.storage_path

    out = lanes.run_backfill(db)
    assert (out["encrypted"], out["failed"], out["remaining"], out["stopped"]) == (3, 0, 0, None)
    assert CRON_FAILED_KEY not in out
    for row in (clean, infected):
        db.refresh(row)
        assert row.enc_version == 1 and _read(row) == DATA
    for row in untouched:
        db.refresh(row)
        assert row.enc_version is None
    db.refresh(att)
    assert att.enc_version == 1

    # The plaintext waits an hour - a ZIP stream may still be on its way to it.
    assert sb.get_storage_backend().exists(plain)
    q = db.query(StoragePurge).filter(StoragePurge.locator == plain).one()
    assert q.not_before > utc_now() + timedelta(minutes=55)

    lr = _last_run(db)
    assert (lr["encrypted"], lr["remaining"], lr["stopped"]) == (3, 0, None)


def test_a_recently_served_file_waits_for_a_later_run(db, stored):
    _enable(db)
    f = stored()
    transfer_activity.mark_download_recent(f.id)
    out = lanes.run_backfill(db)
    assert (out["encrypted"], out["skipped"], out["remaining"]) == (0, 1, 1)
    assert CRON_FAILED_KEY not in out, "nothing failed; it is only waiting"
    db.refresh(f)
    assert f.enc_version is None


@pytest.fixture
def broken_writes(monkeypatch):
    """Breaks writes; `.mend()` restores ONLY them - `monkeypatch.undo()` would
    also undo conftest's Redis isolation and reach for a real Redis."""
    backend = sb.get_storage_backend()
    real = backend.write_stream

    def _broken(_loc, _reader):
        raise OSError("device error")

    monkeypatch.setattr(backend, "write_stream", _broken)

    class _Ctl:
        @staticmethod
        def mend():
            monkeypatch.setattr(backend, "write_stream", real)

    return _Ctl


def test_three_failures_defer_a_file_for_a_day(db, stored, broken_writes, _isolate_encryption_lanes):
    _enable(db)
    f = stored()
    for attempt in range(1, 4):
        out = lanes.run_backfill(db)
        assert out["failed"] == 1 and out[CRON_FAILED_KEY] is True, attempt
    assert out["deferred"] == 1
    (row,) = _audit(db, AuditEventType.file_encryption_failed)
    assert (row.target_type, row.target_id, row.extra) == ("file", f.id, {"reason": "OSError", "attempts": 3})
    assert fe.status(db)["deferred"] == 1

    out = lanes.run_backfill(db)
    assert (out["failed"], out["skipped"]) == (0, 1), "deferred: not tried again"
    assert CRON_FAILED_KEY not in out
    assert db.query(StoragePurge).count() == 0, "every failed attempt cleaned up after itself"

    broken_writes.mend()
    lanes.clear_deferrals()
    assert lanes.run_backfill(db)["encrypted"] == 1


def test_a_success_between_failures_resets_the_count(db, stored, _isolate_encryption_lanes):
    _enable(db)
    a = stored(name="a")
    stored(name="b")
    lanes._record_failure(lanes._member("file", a.id))
    lanes._record_failure(lanes._member("file", a.id))
    assert lanes.run_backfill(db)["encrypted"] == 2
    assert _isolate_encryption_lanes["hashes"].get(lanes._FAILURES_KEY, {}) == {}


def test_low_space_stops_the_run_and_fails_it(db, stored, monkeypatch):
    from app.services import storage

    _enable(db)
    stored(name="a")
    stored(name="b")
    monkeypatch.setattr(storage, "get_disk_stats", lambda _p: {"free_bytes": 100, "total_bytes": 10**9,
                                                               "used_bytes": 0, "percent_free": 0.1})
    out = lanes.run_backfill(db)
    assert (out["encrypted"], out["stopped"], out["remaining"]) == (0, "insufficient_space", 2)
    assert out[CRON_FAILED_KEY] is True
    assert _audit(db, AuditEventType.file_encryption_failed) == [], "not the file's fault"


def test_the_budget_ends_a_run_and_the_rest_waits(db, stored):
    _enable(db)
    for i in range(3):
        stored(name=str(i))
    ticks = iter(range(0, 1000, 10))
    out = lanes.run_backfill(db, budget_sec=15, clock=lambda: next(ticks))
    assert (out["stopped"], out["remaining"]) == ("budget", 3 - out["encrypted"])
    assert 0 < out["encrypted"] < 3
    assert CRON_FAILED_KEY not in out


def test_a_second_run_finds_the_lane_busy(db, stored, _isolate_encryption_lanes):
    _enable(db)
    stored()
    lease = lanes.RedisLease(lanes._BACKFILL_LOCK)
    assert lease.acquire()
    try:
        assert lanes.run_backfill(db)["skipped"] == "busy"
    finally:
        lease.release()


def test_a_cancel_stops_the_run(db, stored):
    _enable(db)
    f = stored()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(file_crypto.EncryptionCancelledError):
        lanes.run_backfill(db, cancel=cancel)
    db.refresh(f)
    assert f.enc_version is None


def test_a_held_file_is_released_by_the_backfill(db, stored):
    """The backstop for a release-lane kick that never arrived."""
    _enable(db)
    held = stored(state=FileState.ready_unscanned, name="held")
    held.release_verdict = "clean"
    db.commit()
    out = lanes.run_backfill(db)
    assert out["held_released"] == {"encrypted": 1}
    db.refresh(held)
    assert (held.state, held.enc_version) == (FileState.clean, 1)


def test_the_cron_is_registered_every_ten_minutes():
    from app.services import cron_schedule
    from app.workers import worker

    assert cron_schedule.REGISTRY[lanes.BACKFILL_JOB].default_interval_min == 10
    (fn,) = [f for f in worker.WorkerSettings.functions if getattr(f, "name", None) == lanes.BACKFILL_JOB]
    assert (fn.timeout_s, fn.max_tries, fn.keep_result_s) == (6 * 3600, 1, 0)


@pytest.mark.asyncio
async def test_the_job_records_a_cron_run(db, stored, monkeypatch):
    from app.models.cron_run import CronRun
    from app.workers import encrypt_at_rest

    _enable(db)
    stored()
    out = await encrypt_at_rest.encrypt_existing_files({})
    assert out["encrypted"] == 1
    assert db.query(CronRun).filter(CronRun.job_name == lanes.BACKFILL_JOB).count() == 1


# --- inbound attachments at ingest -------------------------------------------------


def _ingest_one(db, content: bytes) -> InboundAttachment | None:
    from app.services import inbound_mail
    from app.services.inbound_parse import ParsedAttachment

    msg = InboundMessage(received_at=utc_now(), sender_email="a@b.c", subject="s", imap_uid=9,
                         uidvalidity=1, classification=MessageClass.normal, has_attachments=True)
    db.add(msg)
    db.flush()
    ok = inbound_mail._store_attachment(db, msg.id, ParsedAttachment(filename="c.pdf",
                                                                   content_type="application/pdf",
                                                                   content=content))
    db.commit()
    return db.query(InboundAttachment).one_or_none() if ok else None


@pytest.fixture
def clamd_clean_stream(monkeypatch):
    from app.services import av_scan
    from app.services.av_scan import ScanResult

    monkeypatch.setattr(av_scan, "scan_stream", lambda _fh: ScanResult(state="clean", signature=None, raw="OK"))


def test_an_attachment_is_stored_encrypted_from_the_first_byte(db, clamd_clean_stream):
    _enable(db)
    content = b"%PDF-1.7 contract " * 1000
    att = _ingest_one(db, content)
    assert att is not None and att.enc_version == 1
    backend = sb.get_storage_backend()
    with backend.open(att.storage_key) as fh:
        raw = fh.read()
    assert raw.startswith(file_crypto.HEADER) and b"contract" not in raw
    with fe.open_plaintext(backend, att.storage_key, fe.cipher_for_attachment(att)) as fh:
        assert fh.read() == content


def test_an_attachment_is_stored_plain_with_the_switch_off(db, clamd_clean_stream):
    att = _ingest_one(db, b"plain")
    assert att is not None and att.enc_version is None


def test_an_attachment_that_cannot_be_encrypted_is_stored_plain_and_audited(db, clamd_clean_stream,
                                                                             broken_writes):
    _enable(db)
    att = _ingest_one(db, b"fallback")
    assert att is not None and att.enc_version is None
    with sb.get_storage_backend().open(att.storage_key) as fh:
        assert fh.read() == b"fallback"
    (row,) = _audit(db, AuditEventType.file_encryption_deferred)
    assert (row.target_type, row.target_id, row.extra) == (
        "inbound_attachment", str(att.id), {"reason": "OSError", "lane": "ingest"})


def test_an_attachment_that_cannot_be_stored_at_all_leaves_no_row(db, clamd_clean_stream, broken_writes,
                                                                   monkeypatch):
    _enable(db)
    backend = sb.get_storage_backend()

    def _no_finalize(_tmp, _loc):
        raise OSError("disk full")

    monkeypatch.setattr(backend, "finalize", _no_finalize)
    assert _ingest_one(db, b"lost") is None
    assert db.query(InboundAttachment).count() == 0
