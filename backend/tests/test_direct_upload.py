"""Direct-upload endpoint (POST /api/uploads/direct).

Covers the streamed-to-disk rewrite (audit M3: bounded memory) end to end, plus
the size-cap 413, since this path previously had no dedicated test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import ShareKind
from app.services import share as share_svc


def _future() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


async def _make_share(db, sender, recipient):
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()
    return share


@pytest.mark.asyncio
async def test_direct_upload_persists_file(make_user, db, client, login_as):
    from app.models.user import UserRole

    sender = make_user(email="up@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = await _make_share(db, sender, recipient)
    token, _ = await login_as("up@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/uploads/direct",
        data={"share_id": share.id},
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["size_bytes"] == 11
    f = db.query(File).filter(File.id == body["file_id"]).one()
    assert f.state == FileState.ready_unscanned
    assert f.original_filename == "hello.txt"
    assert f.storage_path


@pytest.mark.asyncio
async def test_direct_upload_rejects_over_cap(make_user, db, client, login_as, monkeypatch):
    from app.models.user import UserRole
    from app.services import settings_registry as sr

    sender = make_user(email="up@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = await _make_share(db, sender, recipient)
    token, _ = await login_as("up@test.local", "Pass12345678!")

    # Force a tiny cap for the direct-upload key only, so a few bytes exceed it
    # (without a 100 MB test payload); leave every other tunable untouched.
    real = sr.effective
    monkeypatch.setattr(
        sr,
        "effective",
        lambda db, key, **k: 4 if key == sr.K.MAX_DIRECT_UPLOAD_BYTES else real(db, key, **k),
    )

    resp = await client.post(
        "/api/uploads/direct",
        data={"share_id": share.id},
        files={"file": ("big.bin", b"way too many bytes", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "DIRECT_UPLOAD_TOO_LARGE"
    # No file row was committed for the rejected upload.
    assert db.query(File).count() == 0
