"""ARQ expire_files cleanup worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.workers.expire_files import expire_files


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _make_expired_share(db, sender, recipient_id: int) -> Share:
    """Bypass create_share's "expiry in past" guard — we need to seed a
    share that's already past its expiry."""
    past = _now_naive() - timedelta(hours=1)
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=past,
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient_id))
    db.commit()
    return share


@pytest.mark.asyncio
async def test_expire_files_transitions_share_and_deletes(make_user, db, tmp_path, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_expired_share(db, sender, recipient.id)

    # Add a file row pointing at a real on-disk file we control.
    on_disk = tmp_path / "file.bin"
    on_disk.write_bytes(b"x" * 1024)
    f = File(
        id="testfile-uuid",
        share_id=share.id,
        original_filename="x.bin",
        mime_type="application/octet-stream",
        size_bytes=1024,
        state=FileState.ready_unscanned,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
    )
    db.add(f)
    db.commit()

    # The worker uses SessionLocal for its own session. Override that to
    # share our test session so we see the same DB.
    from app.workers import expire_files as ef_module
    monkeypatch.setattr(ef_module, "SessionLocal", lambda: db)

    result = await expire_files(None)
    assert result["expired_shares"] == 1
    assert result["deleted_files"] == 1
    assert not on_disk.exists()
    # Re-fetch — the worker's commit detached our previous handles.
    share_after = db.query(Share).filter(Share.id == share.id).one()
    f_after = db.query(File).filter(File.id == f.id).one()
    assert share_after.state == ShareState.expired
    assert f_after.state == FileState.deleted


@pytest.mark.asyncio
async def test_expire_files_idempotent(make_user, db, tmp_path, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _make_expired_share(db, sender, recipient.id)

    from app.workers import expire_files as ef_module
    monkeypatch.setattr(ef_module, "SessionLocal", lambda: db)

    r1 = await expire_files(None)
    r2 = await expire_files(None)
    assert r1["expired_shares"] == 1
    # Re-run picks up nothing — idempotent.
    assert r2["expired_shares"] == 0
    assert r2["deleted_files"] == 0


@pytest.mark.asyncio
async def test_expire_files_skips_active_shares(make_user, db, tmp_path, monkeypatch):
    from app.services import share as share_svc

    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    future = _now_naive() + timedelta(hours=1)

    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=future,
    )
    db.commit()

    from app.workers import expire_files as ef_module
    monkeypatch.setattr(ef_module, "SessionLocal", lambda: db)

    result = await expire_files(None)
    assert result["expired_shares"] == 0
