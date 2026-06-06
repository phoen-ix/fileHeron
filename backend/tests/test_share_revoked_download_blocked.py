"""Regression: authenticated downloads must honour share lifecycle state.

Audit finding H1 - `revoke_share` only flips `share.state`; it leaves the
bytes + `file.state` intact. The authenticated download path
(`/api/files/{id}/download` and `/download-url`) checked only `file.state`,
never `share.state`, so a recipient of a REVOKED (or expired) share could
keep downloading. The public path already gated on share state via
`assert_link_usable`; this brings the authed path in line.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole


def _setup(make_user, db, monkeypatch, *, state=ShareState.active, expires_in_days=1):
    sender = make_user(email="hr@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="rec@test.local", role=UserRole.client, password="Pass12345678!")

    storage_dir = tempfile.mkdtemp(prefix="fh-test-storage-")
    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings, "STORAGE_ROOT", storage_dir
    )
    expires_at = (
        datetime.now(tz=timezone.utc) + timedelta(days=expires_in_days)
    ).replace(tzinfo=None)
    share = Share(
        kind=ShareKind.outbound,
        state=state,
        created_by_id=sender.id,
        expires_at=expires_at,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))

    abs_path = Path(storage_dir) / "f.bin"
    abs_path.write_bytes(b"file bytes for download")
    file_row = File(
        id="00000000-0000-0000-0000-000000000ccc",
        share_id=share.id,
        original_filename="hello.txt",
        mime_type="text/plain",
        size_bytes=23,
        storage_path=str(abs_path),
        state=FileState.clean,
        uploaded_by_id=sender.id,
    )
    db.add(file_row)
    db.commit()
    return sender, recipient, share, file_row


@pytest.mark.asyncio
async def test_active_share_downloads(make_user, db, client, login_as, monkeypatch):
    _, _, _, file_row = _setup(make_user, db, monkeypatch)
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/download", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_revoked_share_blocks_download(make_user, db, client, login_as, monkeypatch):
    _, _, share, file_row = _setup(make_user, db, monkeypatch)
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Recipient can mint + download while active.
    assert (await client.get(f"/api/files/{file_row.id}/download-url", headers=headers)).status_code == 200

    # Owner revokes the share.
    share.state = ShareState.revoked
    db.commit()

    # Both the mint endpoint and the direct download must now refuse.
    mint = await client.get(f"/api/files/{file_row.id}/download-url", headers=headers)
    assert mint.status_code == 410, mint.text
    assert mint.json()["code"] == "SHARE_NOT_ACTIVE"

    dl = await client.get(f"/api/files/{file_row.id}/download", headers=headers)
    assert dl.status_code == 410, dl.text
    assert dl.json()["code"] == "SHARE_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_expired_share_blocks_download(make_user, db, client, login_as, monkeypatch):
    # state still 'active' but expires_at in the past (cron hasn't run yet).
    _, _, share, file_row = _setup(make_user, db, monkeypatch, expires_in_days=1)
    share.expires_at = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    db.commit()
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/download", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 410, r.text
    assert r.json()["code"] == "SHARE_EXPIRED"
