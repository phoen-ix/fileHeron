"""Regression: quarantine must commit the infected+revoked state BEFORE the
irreversible on-disk move. Previously the move + quota release happened first
and the caller committed the state, so a commit failure (or a move that ran
before the commit) could leave an infected file the DB still believed clean.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import quarantine as q_svc


def test_state_durable_even_when_move_fails(make_user, db, monkeypatch):
    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    db.add(File(
        id="q-fail", share_id=share.id, original_filename="bad.bin", size_bytes=5,
        state=FileState.clean, storage_path="/data/files/orig.bin", uploaded_by_id=owner.id,
    ))
    db.commit()

    class FakeBackend:
        def exists(self, _p):
            return True

        def quarantine_locator(self, sid, name):
            return f"quarantine/{sid}/{name}"

        def move(self, _src, _dst):
            raise OSError("simulated move failure")

    monkeypatch.setattr(q_svc, "get_storage_backend", lambda: FakeBackend())
    monkeypatch.setattr(q_svc, "release_bytes", lambda **k: None)

    q_svc.quarantine_file(db, file=db.get(File, "q-fail"), signature="X")

    db.expire_all()
    row = db.get(File, "q-fail")
    assert row.state == FileState.infected                 # committed despite the move failure
    assert row.storage_path == "/data/files/orig.bin"      # unchanged - bytes still at source
    assert db.get(Share, share.id).state == ShareState.revoked
