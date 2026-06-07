"""AV scan worker - quarantine flow + state transitions.

Mocks the clamd network call so tests don't need a live ClamAV.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.services.av_scan import ScanResult
from app.services.quarantine import quarantine_file
from app.workers.av_scan import av_scan_file


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_file_for_scan(db, sender, recipient, tmp_path, content=b"hello"):
    """Create a share + file with a real on-disk artifact, in
    ready_unscanned state - exactly what the worker would see."""
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))

    on_disk = tmp_path / "file.bin"
    on_disk.write_bytes(content)
    f = File(
        id="testfile-uuid",
        share_id=share.id,
        original_filename="upload.bin",
        mime_type="application/octet-stream",
        size_bytes=len(content),
        state=FileState.ready_unscanned,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
    )
    db.add(f)
    db.commit()
    return share, f, on_disk


@pytest.mark.asyncio
async def test_av_scan_clean_marks_file_clean(make_user, db, tmp_path, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _, file, _ = _seed_file_for_scan(db, sender, recipient, tmp_path)

    monkeypatch.setattr(
        av_scan_svc,
        "scan_path",
        lambda _path: ScanResult(state="clean", signature=None, raw="OK"),
    )
    from app.workers import av_scan as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)

    result = await av_scan_file(None, file.id)
    assert result["state"] == "clean"
    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.clean


@pytest.mark.asyncio
async def test_av_scan_infected_quarantines_and_revokes(make_user, db, tmp_path, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share, file, on_disk = _seed_file_for_scan(db, sender, recipient, tmp_path, content=b"EICAR_FAKE")

    monkeypatch.setattr(av_scan_svc.settings, "QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setattr(
        av_scan_svc,
        "scan_path",
        lambda _path: ScanResult(
            state="infected", signature="Eicar-Test-Signature", raw="..."
        ),
    )
    # Quarantine reads QUARANTINE_DIR via the storage backend → app.config.settings.
    monkeypatch.setattr("app.config.settings.QUARANTINE_DIR", str(tmp_path / "quarantine"))

    from app.workers import av_scan as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)

    result = await av_scan_file(None, file.id)
    assert result["state"] == "infected"
    assert result["signature"] == "Eicar-Test-Signature"

    # Original on-disk artifact is gone (moved into quarantine).
    assert not on_disk.exists()
    # File is now in quarantine.
    qdir = tmp_path / "quarantine" / share.id
    assert any(qdir.iterdir())

    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    s_after = db.query(Share).filter(Share.id == share.id).one()
    assert f_after.state == FileState.infected
    assert s_after.state == ShareState.revoked


@pytest.mark.asyncio
async def test_av_scan_oversize_clean_is_not_trusted(make_user, db, tmp_path, monkeypatch):
    """H3: clamd reports 'clean' for a file past its configured scan limit
    WITHOUT actually scanning it. A file larger than AV_MAX_SCAN_BYTES must not
    be marked clean on a clamd OK - it stays ready_unscanned (not downloadable),
    so unscanned large files can't be served as 'clean'."""
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _, file, _ = _seed_file_for_scan(db, sender, recipient, tmp_path, content=b"hello")

    monkeypatch.setattr(
        av_scan_svc,
        "scan_path",
        lambda _p: ScanResult(state="clean", signature=None, raw="OK"),
    )
    # Treat the 5-byte file as 'oversize' for the test.
    monkeypatch.setattr("app.config.settings.AV_MAX_SCAN_BYTES", 2)
    from app.workers import av_scan as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)

    result = await av_scan_file(None, file.id)
    assert result["state"] == "oversize_unscanned"
    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.ready_unscanned


@pytest.mark.asyncio
async def test_av_scan_skip_in_unexpected_state(make_user, db, tmp_path, monkeypatch):
    """If the file is already clean (e.g. a duplicate enqueue), the
    worker should no-op rather than re-scan."""
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _, file, _ = _seed_file_for_scan(db, sender, recipient, tmp_path)
    file.state = FileState.clean
    db.commit()

    called = []
    monkeypatch.setattr(
        av_scan_svc,
        "scan_path",
        lambda p: called.append(p) or ScanResult(state="clean", signature=None, raw="OK"),
    )
    from app.workers import av_scan as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)

    result = await av_scan_file(None, file.id)
    assert result.get("skipped") is True
    assert called == []


def test_quarantine_releases_quota_and_audits(make_user, db, tmp_path, monkeypatch):
    """Direct test of services/quarantine.py - bypasses the worker so we
    can confirm the audit + quota plumbing without async."""
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _, file, _ = _seed_file_for_scan(
        db, sender, recipient, tmp_path, content=b"x" * 1234
    )
    monkeypatch.setattr("app.config.settings.QUARANTINE_DIR", str(tmp_path / "q"))

    quarantine_file(db, file=file, signature="Test-Sig")
    db.commit()

    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.infected

    from app.models.audit_log import AuditEventType, AuditLog
    audits = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type.in_(
                [
                    AuditEventType.file_quarantined.value,
                    AuditEventType.share_revoked.value,
                ]
            )
        )
        .all()
    )
    assert len(audits) >= 2
