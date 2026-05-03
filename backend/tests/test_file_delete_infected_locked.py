"""DELETE /api/files/{id} on an infected file is admin-only.

ClamAV-flagged files belong to the admin's forensic surface — the
uploader shouldn't be able to delete the row out from under
/admin/quarantine. Also pins the no-double-release-of-quota fix in
services/file.py::hard_delete: quarantine_file already released the
bytes, so a subsequent admin-initiated hard_delete must NOT release
again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_infected_file(db, sender, recipient, tmp_path) -> tuple[Share, File]:
    """Build an active share with one file already at state=infected.
    Mirrors the post-quarantine state without invoking the AV worker."""
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="t",
        message=None,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.revoked,  # AV would have auto-revoked the share
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    on_disk = tmp_path / "infected.bin"
    on_disk.write_bytes(b"EICAR-fake")
    f = File(
        id="infected-uuid",
        share_id=share.id,
        original_filename="malware.bin",
        mime_type="application/octet-stream",
        size_bytes=10,
        state=FileState.infected,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
        finalized_at=_now_naive(),
    )
    db.add(f)
    db.commit()
    return share, f


@pytest.mark.asyncio
async def test_owner_cannot_delete_infected_file(
    make_user, db, client, login_as, tmp_path
):
    sender = make_user(
        email="up@test.local", role=UserRole.employee, password="Pass12345678!"
    )
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)
    token, _ = await login_as("up@test.local", "Pass12345678!")

    resp = await client.delete(
        f"/api/files/{file.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "FILE_QUARANTINED_ADMIN_ONLY"

    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.infected
    assert f_after.storage_path == str(tmp_path / "infected.bin")


@pytest.mark.asyncio
async def test_admin_can_delete_infected_file(
    make_user, db, client, login_as, tmp_path
):
    sender = make_user(email="up@test.local", role=UserRole.employee)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.delete(
        f"/api/files/{file.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text
    db.expire_all()
    assert db.query(File).filter(File.id == file.id).one().state == FileState.deleted


@pytest.mark.asyncio
async def test_admin_delete_infected_does_not_release_quota_again(
    make_user, db, client, login_as, tmp_path, monkeypatch
):
    """quarantine_file already released the bytes when it moved the
    file to quarantine. hard_delete on a state=infected file must NOT
    release again — otherwise the user's Redis counter drifts."""
    from app.services import file as file_svc

    sender = make_user(email="up@test.local", role=UserRole.employee)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)

    calls: list[tuple] = []
    monkeypatch.setattr(
        file_svc,
        "release_bytes",
        lambda *, user_id, bytes_to_free: calls.append((user_id, bytes_to_free)),
    )

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.delete(
        f"/api/files/{file.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    assert calls == [], (
        "release_bytes was called from hard_delete on an already-quarantined "
        f"file; expected zero calls (quarantine_file did it), got {calls}"
    )


@pytest.mark.asyncio
async def test_clean_file_delete_still_releases_quota_normally(
    make_user, db, client, login_as, tmp_path, monkeypatch
):
    """Sanity: the no-double-release fix only suppresses the call when
    the file was infected. Normal user deletes of clean files still
    release quota."""
    from app.services import file as file_svc

    sender = make_user(
        email="up@test.local", role=UserRole.employee, password="Pass12345678!"
    )
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="t",
        message=None,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    on_disk = tmp_path / "clean.bin"
    on_disk.write_bytes(b"hi")
    f = File(
        id="clean-uuid",
        share_id=share.id,
        original_filename="ok.bin",
        mime_type="application/octet-stream",
        size_bytes=2,
        state=FileState.clean,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
        finalized_at=_now_naive(),
    )
    db.add(f)
    db.commit()

    calls: list[tuple] = []
    monkeypatch.setattr(
        file_svc,
        "release_bytes",
        lambda *, user_id, bytes_to_free: calls.append((user_id, bytes_to_free)),
    )

    token, _ = await login_as("up@test.local", "Pass12345678!")
    resp = await client.delete(
        f"/api/files/{f.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    assert calls == [(sender.id, 2)]
