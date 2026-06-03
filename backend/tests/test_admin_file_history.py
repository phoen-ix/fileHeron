"""Admin file history endpoint (post-Phase 10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.download_log import DownloadLog, DownloadVia
from app.models.file import File, FileState
from app.models.share import ShareKind, ShareState
from app.models.user import UserRole
from app.services import share as share_svc


def _future(days: int = 7) -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)


def _make_file(
    db,
    *,
    share,
    uploader,
    name: str = "f.bin",
    size: int = 1024,
    state: FileState = FileState.clean,
) -> File:
    f = File(
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=size,
        uploaded_by_id=uploader.id,
        state=state,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@pytest.mark.asyncio
async def test_admin_can_list_files(make_user, db, client, login_as):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="Q3 Reports",
        message=None,
    )
    f = _make_file(db, share=share, uploader=admin, name="report.pdf", size=4096)
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/files",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["file_id"] == f.id
    assert item["filename"] == "report.pdf"
    assert item["size_bytes"] == 4096
    assert item["share_subject"] == "Q3 Reports"
    assert item["uploader"]["id"] == admin.id
    assert item["download_count"] == 0
    assert item["last_downloaded_at"] is None
    assert "(client)" in item["recipients_summary"]


@pytest.mark.asyncio
async def test_admin_files_aggregates_download_log(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="anything",
        message=None,
    )
    f = _make_file(db, share=share, uploader=admin)

    base = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    for i in range(3):
        db.add(
            DownloadLog(
                file_id=f.id,
                share_id=share.id,
                accessed_by_user_id=rec.id,
                accessed_at=base - timedelta(hours=i),
                bytes_served=1024,
                via=DownloadVia.auth,
            )
        )
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/files",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["download_count"] == 3
    assert item["last_downloaded_at"] is not None


@pytest.mark.asyncio
async def test_admin_files_includes_deleted(make_user, db, client, login_as):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="gone",
        message=None,
    )
    share.state = ShareState.expired
    _make_file(db, share=share, uploader=admin, state=FileState.deleted)
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/files?state=deleted",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["state"] == "deleted"
    assert body["items"][0]["share_state"] == "expired"


@pytest.mark.asyncio
async def test_admin_files_filter_by_uploader(make_user, db, client, login_as):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    other = make_user(email="other@test.local", role=UserRole.employee)
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="x",
        message=None,
    )
    _make_file(db, share=share, uploader=admin, name="admin-file.bin")
    _make_file(db, share=share, uploader=other, name="other-file.bin")
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/admin/files?uploader_id={other.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["uploader"]["id"] == other.id


@pytest.mark.asyncio
async def test_admin_files_search_q(make_user, db, client, login_as):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="x",
        message=None,
    )
    _make_file(db, share=share, uploader=admin, name="quarterly-report.pdf")
    _make_file(db, share=share, uploader=admin, name="logs.tar.gz")
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/files?q=quarter",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert "quarterly" in body["items"][0]["filename"]


@pytest.mark.asyncio
async def test_admin_files_admin_only(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/files",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_files_pagination(make_user, db, client, login_as):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="x",
        message=None,
    )
    for i in range(5):
        _make_file(db, share=share, uploader=admin, name=f"f-{i}.bin")
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    r1 = await client.get(
        "/api/admin/files?page_size=2&page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    r3 = await client.get(
        "/api/admin/files?page_size=2&page=3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["total"] == 5
    assert len(r1.json()["items"]) == 2
    assert len(r3.json()["items"]) == 1
