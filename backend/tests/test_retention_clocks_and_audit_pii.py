"""Two clocks that measured the wrong thing, and PII in a table we keep forever.

files-7   `purge_old_quarantine` aged its retention window off `finalized_at` -
          the UPLOAD time - while both the tunable and the worker's own
          docstring say "quarantined longer than". For anything that sat clean
          for a while before a signature update caught it those are different
          dates, so a file quarantined yesterday from a six-month-old upload was
          purged on its very first nightly run: the evidence destroyed before an
          admin had a working day to look at it.

flow-emailchange-3  `audit_log` is deliberately retained through erasure, and
          the code comment justified that by saying every row "references the
          user by anonymised id". Two event types do not: `email_changed` and
          `email_change_requested` carry the person's plaintext addresses in
          their metadata, so /admin/audit and its CSV export handed back an
          erased subject's real addresses indefinitely - after an erasure that
          issued a signed receipt saying they were gone.

From the 2026-07-30 audit.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services.audit import record_audit_event
from app.utils.timeutil import utc_now


@pytest.fixture
def owner(db, make_user):
    return make_user(email="owner@test.local", role=UserRole.employee)


@pytest.fixture
def share(db, owner):
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.commit()
    return sh


def _infected(db, share, owner, tmp_path, *, uploaded_days_ago: int, fid: str):
    p = tmp_path / f"{fid}.bin"
    p.write_bytes(b"malware")
    f = File(
        id=fid, share_id=share.id, original_filename="bad.exe",
        mime_type="application/octet-stream", size_bytes=7, storage_path=str(p),
        state=FileState.infected, uploaded_by_id=owner.id,
        finalized_at=utc_now() - timedelta(days=uploaded_days_ago),
    )
    db.add(f)
    db.commit()
    return f, p


# --- files-7 ----------------------------------------------------------------


def test_a_recently_quarantined_old_upload_is_not_purged(db, share, owner, tmp_path, monkeypatch):
    """The defect: a six-month-old upload caught by a new signature today was
    destroyed on the first nightly run, because the clock read the upload date."""
    from app.workers import purge_old_quarantine as pq

    f, path = _infected(
        db, share, owner, tmp_path, uploaded_days_ago=180,
        fid="00000000-0000-0000-0000-00000000q001",
    )
    record_audit_event(
        db, event_type=AuditEventType.file_quarantined, actor_user_id=None,
        target_type="file", target_id=f.id, metadata={},
    )
    db.commit()

    monkeypatch.setattr(
        "app.services.storage_backend.get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(lambda loc: path.unlink())})(),
    )

    import asyncio

    asyncio.run(pq.purge_old_quarantine(None))
    db.expire_all()
    assert db.query(File).one().storage_path is not None, (
        "evidence destroyed the night it was quarantined"
    )


def test_a_long_quarantined_file_is_still_purged(db, share, owner, tmp_path, monkeypatch):
    """Control: the worker has to keep doing its job."""
    from app.workers import purge_old_quarantine as pq

    f, path = _infected(
        db, share, owner, tmp_path, uploaded_days_ago=400,
        fid="00000000-0000-0000-0000-00000000q002",
    )
    row = record_audit_event(
        db, event_type=AuditEventType.file_quarantined, actor_user_id=None,
        target_type="file", target_id=f.id, metadata={},
    )
    db.commit()
    # Backdate the quarantine event well past the window.
    db.query(AuditLog).filter(AuditLog.id == row.id).update(
        {"created_at": utc_now() - timedelta(days=365)}
    )
    db.commit()

    deleted = []
    monkeypatch.setattr(
        "app.services.storage_backend.get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(lambda loc: deleted.append(loc))})(),
    )

    import asyncio

    asyncio.run(pq.purge_old_quarantine(None))
    assert deleted, "a genuinely stale quarantined file was left on disk"


def test_a_file_with_no_quarantine_event_falls_back(db, share, owner, tmp_path, monkeypatch):
    """Rows quarantined before the audit event existed must keep the old
    behaviour rather than becoming immortal."""
    from app.workers import purge_old_quarantine as pq

    _infected(
        db, share, owner, tmp_path, uploaded_days_ago=400,
        fid="00000000-0000-0000-0000-00000000q003",
    )
    deleted = []
    monkeypatch.setattr(
        "app.services.storage_backend.get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(lambda loc: deleted.append(loc))})(),
    )

    import asyncio

    asyncio.run(pq.purge_old_quarantine(None))
    assert deleted


# --- flow-emailchange-3 -----------------------------------------------------


def test_erasure_scrubs_addresses_from_retained_audit_rows(db, make_user):
    from app.services import erasure

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="now@test.local", role=UserRole.client)
    record_audit_event(
        db, event_type=AuditEventType.email_changed, actor_user_id=admin.id,
        target_type="user", target_id=str(victim.id),
        metadata={"old_email": "before@test.local", "new_email": "now@test.local"},
    )
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.email_changed.value)
        .one()
    )
    assert row.extra["old_email"] == "[erased]"
    assert row.extra["new_email"] == "[erased]"
    assert result["pii_purged"]["audit_log_scrubbed"] == 1


def test_the_audit_event_itself_survives(db, make_user):
    """Control: the row is the legal record the erasure receipt verifies
    against. Scrub the person, keep the fact."""
    from app.services import erasure

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="now@test.local", role=UserRole.client)
    record_audit_event(
        db, event_type=AuditEventType.email_changed, actor_user_id=admin.id,
        target_type="user", target_id=str(victim.id),
        metadata={"old_email": "a@test.local", "new_email": "now@test.local", "via": "admin"},
    )
    db.commit()
    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.email_changed.value)
        .one()
    )
    assert row.extra["via"] == "admin", "non-PII metadata was collateral damage"
