"""cleanup_stale_uploads reaper: abandoned `uploading` files + failed shares."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.workers.cleanup_stale_uploads import cleanup_stale_uploads


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _make_active_share(db, sender) -> Share:
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="Test",
        expires_at=None,
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    return share


def _add_file(db, share, sender, *, state, age_hours, storage_path=None) -> File:
    f = File(
        share_id=share.id,
        original_filename="big.iso",
        mime_type="application/octet-stream",
        size_bytes=1024,
        state=state,
        storage_path=storage_path,
        uploaded_by_id=sender.id,
        created_at=_now_naive() - timedelta(hours=age_hours),
    )
    db.add(f)
    db.flush()
    return f


@pytest.mark.asyncio
async def test_stale_upload_reaped_and_share_failed(make_user, db, tmp_path, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    share = _make_active_share(db, sender)
    on_disk = tmp_path / "partial.bin"
    on_disk.write_bytes(b"x" * 1024)
    f = _add_file(
        db, share, sender,
        state=FileState.uploading, age_hours=5, storage_path=str(on_disk),
    )
    db.commit()

    from app.workers import cleanup_stale_uploads as mod
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await cleanup_stale_uploads(None)
    assert result["files_reaped"] == 1
    assert result["shares_failed"] == 1
    assert not on_disk.exists()  # partial bytes unlinked

    f_after = db.query(File).filter(File.id == f.id).one()
    share_after = db.query(Share).filter(Share.id == share.id).one()
    assert f_after.state == FileState.deleted
    assert share_after.state == ShareState.failed
    assert share_after.terminated_at is not None


@pytest.mark.asyncio
async def test_fresh_upload_left_alone(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    share = _make_active_share(db, sender)
    f = _add_file(db, share, sender, state=FileState.uploading, age_hours=0)
    db.commit()

    from app.workers import cleanup_stale_uploads as mod
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await cleanup_stale_uploads(None)
    assert result["files_reaped"] == 0
    assert result["shares_failed"] == 0
    f_after = db.query(File).filter(File.id == f.id).one()
    share_after = db.query(Share).filter(Share.id == share.id).one()
    assert f_after.state == FileState.uploading
    assert share_after.state == ShareState.active


@pytest.mark.asyncio
async def test_multifile_share_with_good_file_stays_active(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    share = _make_active_share(db, sender)
    good = _add_file(db, share, sender, state=FileState.clean, age_hours=5)
    stale = _add_file(db, share, sender, state=FileState.uploading, age_hours=5)
    db.commit()

    from app.workers import cleanup_stale_uploads as mod
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await cleanup_stale_uploads(None)
    assert result["files_reaped"] == 1
    assert result["shares_failed"] == 0  # share keeps its clean file

    db.expire_all()
    assert db.query(File).filter(File.id == stale.id).one().state == FileState.deleted
    assert db.query(File).filter(File.id == good.id).one().state == FileState.clean
    assert db.query(Share).filter(Share.id == share.id).one().state == ShareState.active


@pytest.mark.asyncio
async def test_idempotent(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    share = _make_active_share(db, sender)
    _add_file(db, share, sender, state=FileState.uploading, age_hours=5)
    db.commit()

    from app.workers import cleanup_stale_uploads as mod
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    r1 = await cleanup_stale_uploads(None)
    r2 = await cleanup_stale_uploads(None)
    assert r1["files_reaped"] == 1
    assert r2["files_reaped"] == 0
    assert r2["shares_failed"] == 0
