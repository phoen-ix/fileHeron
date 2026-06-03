"""Admin quarantine flow — release + purge + download.

Service-level tests for `services/quarantine_admin.py` and an API-level
test for the download endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import quarantine as q_svc
from app.services import quarantine_admin as qadmin_svc


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_share_with_quarantined_file(
    db,
    sender,
    tmp_path,
    *,
    revoke_reason: str = "av_quarantine",
    content: bytes = b"EICAR_FAKE",
    file_id: str = "qf-uuid-1",
    monkeypatch=None,
):
    """Build a share + file, run quarantine_file to get to infected
    state. Returns (share, file)."""
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

    on_disk = tmp_path / f"{file_id}.bin"
    on_disk.write_bytes(content)
    f = File(
        id=file_id,
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

    qdir = tmp_path / "quarantine"
    if monkeypatch is not None:
        monkeypatch.setattr(q_svc.settings, "QUARANTINE_DIR", str(qdir))
    q_svc.quarantine_file(db, file=f, signature="Eicar-Test-Signature")
    db.commit()
    db.refresh(f)
    db.refresh(share)

    if revoke_reason != "av_quarantine":
        # Override the audit row's reason so release won't try to restore.
        last = (
            db.query(AuditLog)
            .filter(
                AuditLog.event_type == AuditEventType.share_revoked.value,
                AuditLog.target_id == share.id,
            )
            .order_by(AuditLog.created_at.desc())
            .first()
        )
        last.extra = {**(last.extra or {}), "reason": revoke_reason}
        db.commit()

    return share, f


def test_release_restores_file_share_and_quota(
    make_user, db, tmp_path, monkeypatch
):
    sender = make_user(email="up@test.local", role=UserRole.employee)
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    monkeypatch.setattr(qadmin_svc, "storage_path_for", lambda fid, **_kw: tmp_path / "out" / f"{fid}.bin")
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )

    # Sanity preconditions.
    assert file.state == FileState.infected
    assert share.state == ShareState.revoked
    quarantined_path = Path(file.storage_path)
    assert quarantined_path.is_file()

    qadmin_svc.release(
        db,
        admin=admin,
        file=file,
        reason="manual review confirmed false positive",
    )
    db.commit()
    db.refresh(file)
    db.refresh(share)

    assert file.state == FileState.clean
    assert share.state == ShareState.active
    assert not quarantined_path.is_file()
    assert Path(file.storage_path).is_file()

    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_quarantine_released.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == admin.id
    assert audits[0].extra["share_restored"] is True
    assert audits[0].extra["reason"] == "manual review confirmed false positive"


def test_release_refuses_when_not_infected(make_user, db, tmp_path, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(email="up@test.local", role=UserRole.employee)
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )
    file.state = FileState.clean
    db.commit()

    with pytest.raises(AppError) as exc:
        qadmin_svc.release(db, admin=admin, file=file, reason="should refuse-1234567890")
    assert exc.value.code == "FILE_NOT_INFECTED"
    assert exc.value.status_code == 409


def test_release_does_not_restore_share_revoked_for_other_reason(
    make_user, db, tmp_path, monkeypatch
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(email="up@test.local", role=UserRole.employee)
    monkeypatch.setattr(qadmin_svc, "storage_path_for", lambda fid, **_kw: tmp_path / "out" / f"{fid}.bin")
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path,
        revoke_reason="manual_admin_action",
        monkeypatch=monkeypatch,
    )
    assert share.state == ShareState.revoked

    qadmin_svc.release(
        db, admin=admin, file=file, reason="manual review of evidence"
    )
    db.commit()
    db.refresh(share)
    db.refresh(file)

    assert file.state == FileState.clean
    assert share.state == ShareState.revoked

    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_quarantine_released.value)
        .all()
    )
    assert audits[0].extra["share_restored"] is False


def test_purge_transitions_to_deleted_and_unlinks_bytes(
    make_user, db, tmp_path, monkeypatch
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(email="up@test.local", role=UserRole.employee)
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )
    on_disk = Path(file.storage_path)
    assert on_disk.is_file()

    qadmin_svc.purge(db, admin=admin, file=file)
    db.commit()
    db.refresh(file)

    # Row drops out of /admin/quarantine (which filters state=infected)
    # and surfaces in /admin/file-history under state=deleted.
    assert file.state == FileState.deleted
    assert file.storage_path is None
    assert not on_disk.is_file()

    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_quarantine_purged.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor_user_id == admin.id
    # No reason field — purge is no longer justification-gated.
    assert "reason" not in audits[0].extra
    assert audits[0].extra["filename"] == "upload.bin"


def test_purge_refuses_when_not_infected(make_user, db, tmp_path, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    sender = make_user(email="up@test.local", role=UserRole.employee)
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )
    file.state = FileState.clean
    db.commit()

    with pytest.raises(AppError) as exc:
        qadmin_svc.purge(db, admin=admin, file=file)
    assert exc.value.code == "FILE_NOT_INFECTED"


@pytest.mark.asyncio
async def test_purge_endpoint_takes_no_body(
    make_user, db, client, login_as, tmp_path, monkeypatch
):
    """Regression: DELETE /api/admin/files/{id}/quarantine accepts no
    body. Posting one anyway is fine (FastAPI ignores it); the key is
    that omitting it doesn't 422."""
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    sender = make_user(email="up@test.local", role=UserRole.employee)
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.delete(
        f"/api/admin/files/{file.id}/quarantine",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text
    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.deleted
    assert f_after.storage_path is None


@pytest.mark.asyncio
async def test_download_returns_bytes_for_infected_file_only(
    make_user, db, client, login_as, tmp_path, monkeypatch
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    sender = make_user(email="up@test.local", role=UserRole.employee)
    share, file = _seed_share_with_quarantined_file(
        db, sender, tmp_path, monkeypatch=monkeypatch
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.get(
        f"/api/admin/files/{file.id}/quarantine/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"EICAR_FAKE"
    cd = resp.headers.get("content-disposition", "")
    assert "upload.bin.quarantined" in cd

    # Flip to clean → should 404.
    file.state = FileState.clean
    db.commit()
    resp2 = await client.get(
        f"/api/admin/files/{file.id}/quarantine/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 404
    assert resp2.json()["code"] == "QUARANTINED_FILE_NOT_FOUND"
