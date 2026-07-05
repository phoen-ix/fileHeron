"""Regression: erasure file deletion must be durable per-file, and the
per-file audit event must attribute the admin actor (not the erased user).

- hard_delete unlinks the bytes BEFORE marking the row deleted. If a later
  file's unlink fails and the whole erasure aborts, a transaction rollback must
  NOT revert (resurrect) the DB rows of files whose bytes are already gone.
- The file_deleted audit written during erasure must record the admin actor.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import erasure as erasure_svc
from app.services import file as file_svc


def _share_with_files(db, owner, specs):
    share = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(share)
    db.flush()
    for fid, path in specs:
        db.add(File(
            id=fid, share_id=share.id, original_filename=fid + ".bin",
            mime_type="application/octet-stream", size_bytes=10,
            state=FileState.clean, storage_path=path, uploaded_by_id=owner.id,
        ))
    db.commit()


def test_mid_batch_unlink_failure_keeps_prior_deletions_committed(make_user, db, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="victim@test.local", role=UserRole.client)
    _share_with_files(db, victim, [("era-ok", "/tmp/ok.bin"), ("era-fail", "/tmp/fail.bin")])

    class FakeBackend:
        def delete(self, path):
            if path.endswith("fail.bin"):
                raise OSError("simulated unlink failure")

    monkeypatch.setattr(file_svc, "get_storage_backend", lambda: FakeBackend())
    monkeypatch.setattr(file_svc, "release_bytes", lambda **k: None)

    with pytest.raises(AppError) as exc:
        erasure_svc.erase_user(db, actor=admin, target=victim)
    assert exc.value.code == "ERASURE_FILE_DELETE_FAILED"

    db.expire_all()
    # The successfully-deleted file's bytes are gone, so its row MUST stay
    # deleted (pre-fix, the rollback resurrected it -> row present, bytes gone).
    assert db.get(File, "era-ok").state == FileState.deleted
    assert db.get(File, "era-fail").state != FileState.deleted


def test_erasure_file_delete_audit_attributed_to_admin(make_user, db, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="victim@test.local", role=UserRole.client)
    # storage_path=None -> hard_delete skips the disk unlink (no backend needed).
    _share_with_files(db, victim, [("era-audit", None)])
    monkeypatch.setattr(file_svc, "release_bytes", lambda **k: None)

    erasure_svc.erase_user(db, actor=admin, target=victim)
    db.commit()

    ev = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_deleted)
        .one()
    )
    assert ev.actor_user_id == admin.id  # the admin, not the erased victim
    assert ev.actor_user_id != victim.id
