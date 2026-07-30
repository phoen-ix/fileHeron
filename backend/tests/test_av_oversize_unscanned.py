"""Oversize files are served as UNSCANNED, never as clean.

clamd stores MaxFileSize in an int and clamps it to INT_MAX (~2 GiB) whatever
clamd.conf says - its own startup log reports "File size limit set to
2147483645 bytes" for a configured `MaxFileSize 30G`. Above that clamd answers
"clean" WITHOUT reading the file.

fileHeron supports uploads far larger than that, so the product decision
(2026-07-30) is to keep serving them while recording the scan gap honestly:
state reaches `clean` so the file is downloadable, but `av_unscanned` is set,
the API exposes it, and an audit row records that a file was released without a
verdict. What must never happen is a bare `clean` that nothing earned.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.services.av_scan import ScanResult
from app.workers.av_scan import av_scan_file


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.fixture
def scanned_file(db, make_user, tmp_path):
    """A ready_unscanned file with real bytes on disk, sized to order."""

    def _make(size_bytes: int) -> File:
        owner = make_user(email="owner@test.local", role=UserRole.employee)
        share = Share(
            created_by_id=owner.id,
            kind=ShareKind.outbound,
            state=ShareState.active,
        )
        db.add(share)
        db.commit()
        path = tmp_path / "big.bin"
        path.write_bytes(b"x")  # real bytes; size_bytes is what the guard reads
        f = File(
            share_id=share.id,
            original_filename="big.bin",
            mime_type="application/octet-stream",
            size_bytes=size_bytes,
            storage_path=str(path),
            state=FileState.ready_unscanned,
            uploaded_by_id=owner.id,
            finalized_at=_now(),
        )
        db.add(f)
        db.commit()
        return f

    return _make


@pytest.fixture
def clamd_says_clean(monkeypatch):
    monkeypatch.setattr(
        av_scan_svc,
        "scan_path",
        lambda _p: ScanResult(state="clean", signature=None, raw="OK"),
    )
    monkeypatch.setattr(settings, "AV_SKIP", False)


@pytest.mark.asyncio
async def test_oversize_file_is_clean_but_flagged(
    db, scanned_file, clamd_says_clean
):
    f = scanned_file(settings.AV_MAX_SCAN_BYTES + 1)

    result = await av_scan_file({}, f.id)

    db.refresh(f)
    # Downloadable...
    assert f.state == FileState.clean
    # ...but explicitly not vouched for.
    assert f.av_unscanned is True
    assert result["av_unscanned"] is True


@pytest.mark.asyncio
async def test_oversize_release_is_audited(db, scanned_file, clamd_says_clean):
    """A scan gap must leave a durable trace, not just a log line."""
    f = scanned_file(settings.AV_MAX_SCAN_BYTES + 1)

    await av_scan_file({}, f.id)

    row = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.file_served_unscanned,
            AuditLog.target_id == f.id,
        )
        .one()
    )
    assert row.extra["size_bytes"] == settings.AV_MAX_SCAN_BYTES + 1


@pytest.mark.asyncio
async def test_normal_file_is_clean_and_not_flagged(
    db, scanned_file, clamd_says_clean
):
    """Control: a scannable file must NOT be tarred with the warning, or the
    flag becomes noise everyone learns to ignore."""
    f = scanned_file(1024)

    await av_scan_file({}, f.id)

    db.refresh(f)
    assert f.state == FileState.clean
    assert f.av_unscanned is False
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_served_unscanned)
        .count()
        == 0
    )


def test_threshold_matches_clamd_int_max():
    """AV_MAX_SCAN_BYTES must not drift back above clamd's real ceiling.

    Raising it does not make clamd scan more - it only makes fileHeron trust
    verdicts clamd never produced, which is exactly the bug this replaced.
    """
    assert settings.AV_MAX_SCAN_BYTES <= 2147483645
