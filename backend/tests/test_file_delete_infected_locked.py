"""DELETE on an infected file is refused - for the uploader AND for an admin.

ClamAV-flagged files belong to the admin's forensic surface: an infected row's
`storage_path` IS the quarantine copy (quarantine_file rewrites it), and the
only sanctioned way to destroy it is `quarantine_admin.purge`, which writes the
`file_quarantine_purged` receipt. The interactive delete routes used to let an
admin through to `hard_delete`, which unlinked the quarantine copy under a
plain `file_deleted` row. Right-to-erasure keeps its opt-in
(`allow_quarantined=True`), and that path must still NOT release quota a
second time: quarantine_file already released the bytes.
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
@pytest.mark.parametrize("path", ["/api/files/{id}", "/api/admin/files/{id}"])
async def test_admin_delete_of_infected_file_is_refused(
    make_user, db, client, login_as, tmp_path, path
):
    """Both interactive admin deletes - the share page's route and the file
    history's - refuse a quarantined file and leave the quarantine copy where
    it is. The Quarantine page is the only place it is released or purged."""
    sender = make_user(email="up@test.local", role=UserRole.employee)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.delete(
        path.format(id=file.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "FILE_QUARANTINED"

    db.expire_all()
    f_after = db.query(File).filter(File.id == file.id).one()
    assert f_after.state == FileState.infected
    assert f_after.storage_path == str(tmp_path / "infected.bin")
    assert (tmp_path / "infected.bin").exists(), "the quarantine copy must survive"


def test_hard_delete_refuses_quarantined_unless_opted_in(
    make_user, db, tmp_path
):
    """The guard lives in the helper, not only in the routes, so a future
    caller cannot reach the quarantine copy by accident."""
    from app.middleware.errors import AppError
    from app.services import file as file_svc

    sender = make_user(email="up@test.local", role=UserRole.employee)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)

    with pytest.raises(AppError) as exc:
        file_svc.hard_delete(db, file=file, reason="anything")
    assert exc.value.status_code == 409
    assert exc.value.code == "FILE_QUARANTINED"
    db.expire_all()
    assert db.query(File).filter(File.id == file.id).one().state == FileState.infected


def test_erasure_path_destroys_quarantined_bytes_without_releasing_quota_again(
    make_user, db, tmp_path, monkeypatch
):
    """Right-to-erasure opts in (`allow_quarantined=True`) because the
    subject's data must go whatever the AV made of it. quarantine_file already
    released the bytes when it moved the file, so this must NOT release again -
    otherwise the user's Redis counter drifts."""
    from app.services import file as file_svc

    sender = make_user(email="up@test.local", role=UserRole.employee)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share, file = _seed_infected_file(db, sender, recipient, tmp_path)

    calls: list[tuple] = []
    monkeypatch.setattr(
        file_svc,
        "release_bytes",
        lambda *, user_id, bytes_to_free: calls.append((user_id, bytes_to_free)),
    )

    file_svc.hard_delete(
        db, file=file, reason="user_erased", allow_quarantined=True
    )
    db.commit()

    assert not (tmp_path / "infected.bin").exists()
    db.expire_all()
    assert db.query(File).filter(File.id == file.id).one().state == FileState.deleted
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
