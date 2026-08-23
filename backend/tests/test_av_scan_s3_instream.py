"""`av_scan_file`'s object-store branch, which no test selected.

`av_scan.py` picks its scan path from `backend.local_path(locator)`:

    if local is not None:   scan_path(local)     # local filesystem
    else:                   scan_stream(open())  # object store, clamd INSTREAM

`local_path` returns None only for S3, and only two files in the whole suite
ever select the S3 backend - neither of them this worker. So the INSTREAM arm
was never executed.

`test_av_scan_instream.py` looks like coverage and is not: it calls
`av_scan.scan_stream(io.BytesIO(...))` against a fake socket, which exercises the
FUNCTION's fail-safe behaviour and never touches the worker, the backend, or the
branch selection. Its twin in `rescan_inbound_attachments.py` is worse - its
tests monkeypatch BOTH `scan_path` and `scan_stream`, so they pass whichever arm
runs.

What that costs if it regresses: on an S3 deployment every upload either fails to
scan or is scanned by the wrong call, and AV is the gate between an upload and a
recipient.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.workers.av_scan import av_scan_file

_BUCKET = "fh-avscan-bucket"
_FILE_ID = "00000000-0000-0000-0000-00000000avs3"[:36]


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setattr("app.config.settings.STORAGE_BACKEND", "s3")
    monkeypatch.setattr("app.config.settings.S3_BUCKET", _BUCKET)
    monkeypatch.setattr("app.config.settings.S3_REGION", "us-east-1")
    monkeypatch.setattr("app.config.settings.S3_ACCESS_KEY_ID", "test")
    monkeypatch.setattr("app.config.settings.S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr("app.config.settings.S3_KEY_PREFIX", "")
    from app.services import storage_backend

    storage_backend.reset_storage_backend_cache()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield
    storage_backend.reset_storage_backend_cache()


@pytest.fixture
def s3_pending_file(db, make_user, s3_env, tmp_path):
    """A `ready_unscanned` row whose bytes live in the bucket."""
    from app.services.storage_backend import get_storage_backend

    owner = make_user(email="avowner@test.local", role=UserRole.employee)
    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
    )
    db.add(sh)
    db.flush()

    backend = get_storage_backend()
    src = tmp_path / "up.part"
    src.write_bytes(b"scan me")
    locator = backend.generate_locator(_FILE_ID)
    backend.finalize(str(src), locator)

    f = File(
        id=_FILE_ID, share_id=sh.id, original_filename="up.txt",
        mime_type="text/plain", size_bytes=7, storage_path=locator,
        state=FileState.ready_unscanned, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return f


@pytest.mark.asyncio
async def test_the_object_store_scan_goes_through_instream(
    db, s3_pending_file, monkeypatch
):
    """The branch selection itself. `scan_path` would be handed a path that does
    not exist on this host."""
    seen = {}

    def _stream(fh):
        seen["bytes"] = fh.read()
        return av_scan_svc.ScanResult(state="clean", signature=None, raw="OK")

    def _path(_p):
        seen["path_called"] = True
        return av_scan_svc.ScanResult(state="clean", signature=None, raw="OK")

    monkeypatch.setattr(av_scan_svc, "scan_stream", _stream)
    monkeypatch.setattr(av_scan_svc, "scan_path", _path)

    await av_scan_file({}, s3_pending_file.id)

    assert "path_called" not in seen, (
        "the worker took the local-filesystem path for an object-store file"
    )
    assert seen.get("bytes") == b"scan me", (
        "INSTREAM ran but did not read the object's bytes"
    )
    db.expire_all()
    assert db.query(File).filter(File.id == _FILE_ID).one().state is FileState.clean


@pytest.mark.asyncio
async def test_an_infected_object_is_quarantined_through_the_same_path(
    db, s3_pending_file, monkeypatch
):
    """The verdict must reach the state machine identically on either arm - an
    INSTREAM scan that cannot quarantine is worse than no scan."""
    monkeypatch.setattr(
        av_scan_svc, "scan_stream",
        lambda fh: av_scan_svc.ScanResult(state="infected", signature="EICAR", raw="FOUND"),
    )
    await av_scan_file({}, s3_pending_file.id)
    db.expire_all()
    assert db.query(File).filter(File.id == _FILE_ID).one().state is FileState.infected


@pytest.mark.asyncio
async def test_the_local_backend_still_takes_scan_path(db, make_user, tmp_path, monkeypatch):
    """The control: this must be the OBJECT-STORE branch, not the worker having
    stopped calling `scan_path` altogether."""
    from app.services.storage_backend import get_storage_backend

    owner = make_user(email="localowner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()

    backend = get_storage_backend()
    fid = "00000000-0000-0000-0000-0000000local"
    src = tmp_path / "l.part"
    src.write_bytes(b"local bytes")
    locator = backend.generate_locator(fid)
    backend.finalize(str(src), locator)
    db.add(File(
        id=fid, share_id=sh.id, original_filename="l.txt", mime_type="text/plain",
        size_bytes=11, storage_path=locator, state=FileState.ready_unscanned,
        uploaded_by_id=owner.id,
    ))
    db.commit()

    seen = {}

    def _path(p):
        seen["path"] = p
        return av_scan_svc.ScanResult(state="clean", signature=None, raw="OK")

    def _stream(_fh):
        pytest.fail("local storage must not use INSTREAM")

    monkeypatch.setattr(av_scan_svc, "scan_path", _path)
    monkeypatch.setattr(av_scan_svc, "scan_stream", _stream)
    await av_scan_file({}, fid)
    assert "path" in seen, "the local path never reached scan_path"
