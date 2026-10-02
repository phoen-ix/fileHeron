"""Encryption at rest on the object store: both lanes stream GetObject ->
encrypt -> multipart upload, never a local temp copy, and the plaintext object
is deleted through the purge queue like a local file. (Serving an encrypted S3
row is pinned in test_encrypted_range_matrix.py.)"""
from __future__ import annotations

import os
from datetime import timedelta

import boto3
import pytest
from moto import mock_aws

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.storage_purge import StoragePurge
from app.models.user import UserRole
from app.services import encryption_lanes as lanes
from app.services import file_encryption as fe
from app.services import settings as settings_svc
from app.services import storage_backend as sb
from app.utils import file_crypto
from app.utils.timeutil import utc_now

_BUCKET = "fh-enc-lanes"
DATA = os.urandom(file_crypto.CHUNK_SIZE * 2 + 5)


@pytest.fixture
def s3(monkeypatch):
    for k, v in {
        "STORAGE_BACKEND": "s3", "S3_BUCKET": _BUCKET, "S3_REGION": "us-east-1",
        "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_KEY_PREFIX": "",
    }.items():
        monkeypatch.setattr(f"app.config.settings.{k}", v)
    sb.reset_storage_backend_cache()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client
    sb.reset_storage_backend_cache()


@pytest.fixture
def row(db, make_user, s3, tmp_path):
    def _make(state: FileState) -> File:
        u = make_user(email=f"s3-{state.value}@test.local", role=UserRole.employee)
        sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
        db.add(sh)
        db.flush()
        backend = sb.get_storage_backend()
        src = tmp_path / f"{state.value}.part"
        src.write_bytes(DATA)
        locator = backend.generate_locator(f"s3-{state.value}")
        backend.finalize(str(src), locator)
        f = File(share_id=sh.id, original_filename="x.bin", size_bytes=len(DATA), uploaded_by_id=u.id,
                 state=state, storage_path=locator, finalized_at=utc_now())
        db.add(f)
        db.commit()
        return f

    settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST, value="true", actor=None)
    db.commit()
    return _make


def _object(s3, key: str) -> bytes:
    return s3.get_object(Bucket=_BUCKET, Key=key)["Body"].read()


def _keys(s3) -> set[str]:
    return {o["Key"] for o in s3.list_objects_v2(Bucket=_BUCKET).get("Contents", [])}


def test_the_release_lane_on_the_object_store(db, row, s3):
    f = row(FileState.ready_unscanned)
    plain = f.storage_path
    assert lanes.hold_for_encryption(db, f, "clean")
    db.commit()

    assert lanes.run_release_lane(db) == {"encrypted": 1}
    db.refresh(f)
    assert (f.state, f.enc_version) == (FileState.clean, 1)
    stored = _object(s3, f.storage_path)
    assert stored.startswith(file_crypto.HEADER) and len(stored) == file_crypto.ciphertext_size(len(DATA))
    assert _keys(s3) == {f.storage_path}, "the plaintext object is gone"
    with fe.open_plaintext(sb.get_storage_backend(), f.storage_path, fe.cipher_for_file(f)) as fh:
        assert fh.read() == DATA
    assert plain not in _keys(s3)


def test_the_backfill_on_the_object_store(db, row, s3):
    f = row(FileState.clean)
    plain = f.storage_path
    out = lanes.run_backfill(db)
    assert (out["encrypted"], out["remaining"]) == (1, 0)
    db.refresh(f)
    assert f.enc_version == 1 and _object(s3, f.storage_path).startswith(file_crypto.HEADER)
    assert plain in _keys(s3), "the plaintext waits out its grace"

    db.query(StoragePurge).update({"not_before": utc_now() - timedelta(seconds=1)})
    db.commit()
    assert fe.sweep_purges(db) == {"purged": 1, "failed": 0}
    assert _keys(s3) == {f.storage_path}


def test_object_stores_skip_the_disk_guard(db, row, monkeypatch):
    """An object store has no free-space reading; the guard must not refuse
    on a stat of the local root."""
    from app.services import storage

    def _would_refuse(_p):
        return {"free_bytes": 0, "total_bytes": 1, "used_bytes": 1, "percent_free": 0.0}

    monkeypatch.setattr(storage, "get_disk_stats", _would_refuse)
    row(FileState.clean)
    assert lanes.run_backfill(db)["encrypted"] == 1
