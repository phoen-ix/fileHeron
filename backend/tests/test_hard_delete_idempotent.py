"""Regression: hard_delete is idempotent — a second call on an already
deleted file must NOT release quota again (finding L11)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import file as file_svc


def _make_clean_file(make_user, db):
    owner = make_user(email="o@test.local", role=UserRole.admin)
    share = Share(kind=ShareKind.outbound, state=ShareState.active, created_by_id=owner.id)
    db.add(share)
    db.flush()
    storage = tempfile.mkdtemp(prefix="fh-hd-")
    p = Path(storage) / "x.bin"
    p.write_bytes(b"bytes")
    f = File(
        id="00000000-0000-0000-0000-0000000000dd",
        share_id=share.id,
        original_filename="x.bin",
        mime_type="application/octet-stream",
        size_bytes=5,
        storage_path=str(p),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return f


def test_hard_delete_releases_quota_once(make_user, db, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        file_svc, "release_bytes", lambda *, user_id, bytes_to_free: calls.append(bytes_to_free)
    )
    f = _make_clean_file(make_user, db)

    file_svc.hard_delete(db, file=f, reason="user_request")
    assert f.state == FileState.deleted
    assert calls == [5]  # released once

    # Second call is a no-op — no double release.
    file_svc.hard_delete(db, file=f, reason="user_request")
    assert calls == [5]
