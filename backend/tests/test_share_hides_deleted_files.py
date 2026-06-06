"""Regression: deleting every file from a share must persist visually.

Bug: a user opened a share they had sent, deleted both files (each
DELETE returned 204, file row → state=deleted, bytes unlinked), the
detail panel showed "Files (0)" via optimistic update, but navigating
away and back made the files reappear. Cause: the share router's
``_to_share_response`` iterated ``share.files`` with no state filter,
so deleted rows kept being echoed in the response.

This test pins the fix in two places - the detail response (file
list + effective subject) and the list response (file_count + total
size)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_share_with_files(db, sender, recipient, *, n: int = 2) -> Share:
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="Phase 3a smoke test",
        message=None,
        expires_at=_now_naive() + timedelta(hours=24),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    for i in range(n):
        f = File(
            id=f"file-uuid-{i}",
            share_id=share.id,
            original_filename=f"smoke-{i}.bin",
            mime_type="application/octet-stream",
            size_bytes=100 * (i + 1),
            state=FileState.clean,
            storage_path=f"/tmp/file-{i}.bin",
            uploaded_by_id=sender.id,
            finalized_at=_now_naive(),
        )
        db.add(f)
    db.commit()
    return share


@pytest.mark.asyncio
async def test_detail_hides_deleted_files(make_user, db, client, login_as):
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share_with_files(db, sender, recipient, n=2)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Both files visible up-front.
    resp = await client.get(f"/api/shares/{share.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["files"]) == 2
    assert body["effective_subject"] == "Phase 3a smoke test"

    # Mark one deleted directly (matches what services/file.py::hard_delete
    # leaves behind: state=deleted, storage_path retained).
    f = db.query(File).filter(File.id == "file-uuid-0").one()
    f.state = FileState.deleted
    db.commit()

    resp2 = await client.get(f"/api/shares/{share.id}", headers=headers)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["files"]) == 1
    assert body2["files"][0]["id"] == "file-uuid-1"

    # Delete the second too - list goes empty, share stays active (deletion
    # doesn't auto-revoke; that's separate behaviour).
    f2 = db.query(File).filter(File.id == "file-uuid-1").one()
    f2.state = FileState.deleted
    db.commit()

    resp3 = await client.get(f"/api/shares/{share.id}", headers=headers)
    assert resp3.status_code == 200
    body3 = resp3.json()
    assert body3["files"] == []
    assert body3["state"] == "active"
    # Subject set explicitly so it stays - the empty-files fallback
    # doesn't kick in here.
    assert body3["effective_subject"] == "Phase 3a smoke test"


@pytest.mark.asyncio
async def test_no_subject_share_keeps_filename_label_after_all_files_deleted(
    make_user, db, client, login_as
):
    """Regression: a share with no explicit subject AND all files
    deleted must still render with an identifiable label so the user
    can find it in /outbox under 'All states'. Falls back to the first
    file's name (deleted-state files are valid tombstones)."""
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share_with_files(db, sender, recipient, n=1)
    share.subject = None  # no explicit subject - fall back to filename
    db.commit()
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Delete the only file.
    f = db.query(File).filter(File.share_id == share.id).one()
    f.state = FileState.deleted
    db.commit()

    # Detail still names the original file.
    resp = await client.get(f"/api/shares/{share.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["files"] == []
    assert body["effective_subject"] == "smoke-0.bin"

    # List view does too - what the user sees as the row title.
    resp2 = await client.get("/api/shares?box=outbox", headers=headers)
    items = resp2.json()["items"]
    row = next(i for i in items if i["id"] == share.id)
    assert row["effective_subject"] == "smoke-0.bin"
    assert row["file_count"] == 0


@pytest.mark.asyncio
async def test_list_count_excludes_deleted_files(make_user, db, client, login_as):
    sender = make_user(email="s@test.local", role=UserRole.employee, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share_with_files(db, sender, recipient, n=2)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Mark both files deleted.
    for f in db.query(File).filter(File.share_id == share.id).all():
        f.state = FileState.deleted
    db.commit()

    resp = await client.get("/api/shares?box=outbox", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    row = next(i for i in items if i["id"] == share.id)
    assert row["file_count"] == 0
    assert row["total_size_bytes"] == 0
