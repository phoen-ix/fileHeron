"""The swap engine of encryption at rest (services/file_encryption.py).

A stored file is never rewritten in place: its other form goes to a new
locator, the row is swapped by a conditional UPDATE, the old bytes are queued.
These walk every crash point and race and check the one invariant that matters:
the row ALWAYS points at complete bytes it can read, and nothing is left on
disk that no queue row knows about.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass
from app.models.share import Share, ShareKind, ShareState
from app.models.storage_purge import PURGE_ENC_PLAINTEXT, PURGE_ENC_TARGET, StoragePurge
from app.models.user import UserRole
from app.services import file_encryption as fe
from app.services import storage_backend as sb
from app.utils import file_crypto
from app.utils.timeutil import utc_now
from tests._encryption_helpers import store_plain

DATA = os.urandom(file_crypto.CHUNK_SIZE * 2 + 99)
NOW = timedelta(0)


@pytest.fixture
def row(db, make_user):
    u = make_user(email="e@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(share_id=sh.id, original_filename="a.bin", size_bytes=len(DATA),
             uploaded_by_id=u.id, state=FileState.clean, storage_path=store_plain(DATA))
    db.add(f)
    db.commit()
    return f


def _read(row) -> bytes:
    with fe.open_plaintext(sb.get_storage_backend(), row.storage_path, fe.cipher_for_file(row)) as fh:
        return fh.read()


def _exists(loc: str) -> bool:
    return sb.get_storage_backend().exists(loc)


def _queue(db):
    return [(r.reason, r.locator) for r in db.query(StoragePurge).order_by(StoragePurge.id)]


def test_encrypt_then_decrypt_round_trip(db, row):
    old = row.storage_path
    assert fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)
    db.refresh(row)
    assert row.enc_version == 1 and row.key_encrypted and row.storage_path != old
    assert row.storage_path.endswith(".fhe")
    assert _read(row) == DATA
    assert not _exists(old), "the plaintext must be gone once its purge is due"
    assert _queue(db) == []

    enc = row.storage_path
    assert fe.rewrite_stored(db, fe.target_for_file(row), encrypt=False, purge_after=NOW)
    db.refresh(row)
    assert row.enc_version is None and row.key_encrypted is None
    assert _read(row) == DATA and not _exists(enc)


def test_with_a_grace_the_replaced_bytes_wait(db, row):
    old = row.storage_path
    fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=timedelta(hours=1))
    assert _exists(old)
    assert _queue(db) == [(PURGE_ENC_PLAINTEXT, old)]
    db.query(StoragePurge).update({"not_before": utc_now() - timedelta(seconds=1)})
    db.commit()
    assert fe.sweep_purges(db) == {"purged": 1, "failed": 0}
    assert not _exists(old)


def test_losing_the_race_changes_nothing_and_queues_our_copy(db, row, monkeypatch):
    """Another writer moved the row while we wrote."""
    backend = sb.get_storage_backend()
    real = backend.write_stream
    moved_to = store_plain(DATA, name="other")

    def write_and_race(loc, reader):
        real(loc, reader)
        db.query(File).filter(File.id == row.id).update({"storage_path": moved_to})
        db.commit()

    monkeypatch.setattr(backend, "write_stream", write_and_race)
    assert fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW) is False
    db.refresh(row)
    assert (row.storage_path, row.enc_version) == (moved_to, None)
    assert _queue(db) == [], "our copy was debris and is already purged"
    assert _read(row) == DATA


def test_a_crash_mid_write_leaves_the_row_alone_and_the_debris_queued(db, row, monkeypatch):
    backend = sb.get_storage_backend()
    real = backend.write_stream
    targets = []

    def write_half(loc, reader):
        targets.append(loc)
        with open(loc, "wb") as fh:
            fh.write(reader.read(1000))
        raise OSError("disk went away")

    monkeypatch.setattr(backend, "write_stream", write_half)
    with pytest.raises(OSError):
        fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)
    monkeypatch.setattr(backend, "write_stream", real)
    db.refresh(row)
    assert row.enc_version is None and _read(row) == DATA
    assert _queue(db) == [(PURGE_ENC_TARGET, targets[0])]
    fe.sweep_purges(db)
    assert not _exists(targets[0]) and _queue(db) == []


def test_a_process_that_dies_after_writing_leaves_a_lease_that_expires(db, row):
    prepared = fe.prepare_rewrite(db, fe.target_for_file(row), encrypt=True)
    # ...and the process dies here: no swap, no finish.
    db.rollback()
    assert fe.sweep_purges(db) == {"purged": 0, "failed": 0}, "a live lease is not debris"
    assert _exists(prepared.new_locator)
    db.query(StoragePurge).update({"not_before": utc_now() - timedelta(seconds=1)})
    db.commit()
    fe.sweep_purges(db)
    assert not _exists(prepared.new_locator)
    db.refresh(row)
    assert row.enc_version is None and _read(row) == DATA


def test_a_failed_purge_is_retried_and_audited_once(db, row, monkeypatch):
    old = row.storage_path
    backend = sb.get_storage_backend()
    monkeypatch.setattr(backend, "delete", lambda _loc: (_ for _ in ()).throw(OSError("busy")))
    fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)
    db.query(StoragePurge).update({"not_before": utc_now() - timedelta(seconds=1)})
    db.commit()
    assert fe.sweep_purges(db)["failed"] == 1
    (q,) = db.query(StoragePurge).all()
    assert q.attempts == 2 and q.not_before > utc_now()
    assert db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.file_purge_failed.value
    ).count() == 1
    monkeypatch.undo()
    db.query(StoragePurge).update({"not_before": utc_now() - timedelta(seconds=1)})
    db.commit()
    fe.sweep_purges(db)
    assert not _exists(old) and _queue(db) == []


def test_a_row_whose_size_is_wrong_is_never_encrypted(db, row):
    row.size_bytes = len(DATA) + 1
    db.commit()
    with pytest.raises(file_crypto.FileIntegrityError):
        fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)
    db.refresh(row)
    assert row.enc_version is None
    fe.sweep_purges(db)
    assert _queue(db) == []


def test_the_state_guard(db, row):
    row.state = FileState.infected
    db.commit()
    assert fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW,
                             expected_states=[FileState.clean]) is False
    db.refresh(row)
    assert row.enc_version is None and _read(row) == DATA


def test_too_little_space_refuses_before_anything_is_written(db, row, monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "get_disk_stats", lambda _p: {"free_bytes": 1000, "total_bytes": 10**9,
                                                               "used_bytes": 0, "percent_free": 0.1})
    with pytest.raises(fe.InsufficientSpaceError):
        fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)
    assert _queue(db) == []


def test_critically_low_storage_refuses(db, row):
    from app.services import settings as settings_svc

    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_CRITICAL_LOW, value="true", actor=None)
    db.commit()
    with pytest.raises(fe.InsufficientSpaceError):
        fe.rewrite_stored(db, fe.target_for_file(row), encrypt=True, purge_after=NOW)


def test_an_inbound_attachment_is_rewritten_by_the_same_engine(db):
    msg = InboundMessage(received_at=utc_now(), sender_email="a@b.c", subject="s", imap_uid=7,
                         uidvalidity=1, classification=MessageClass.normal, has_attachments=True)
    db.add(msg)
    db.flush()
    att = InboundAttachment(message_id=msg.id, filename="x.pdf", size_bytes=len(DATA),
                            storage_key=store_plain(DATA), av_state=AttachmentAVState.clean)
    db.add(att)
    db.commit()
    assert fe.rewrite_stored(db, fe.target_for_attachment(att), encrypt=True, purge_after=NOW)
    db.refresh(att)
    assert att.enc_version == 1
    with fe.open_plaintext(sb.get_storage_backend(), att.storage_key, fe.cipher_for_attachment(att)) as fh:
        assert fh.read() == DATA
