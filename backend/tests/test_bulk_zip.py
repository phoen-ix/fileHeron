"""Bulk ZIP download - authed (mint + consume) and public endpoints.

Mirrors test_share_download_limit.py's on-disk file seeding. One ZIP =
one download-budget decrement; the archive contains every `clean` file.
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import public_link as public_link_svc


def _naive_future(days: int = 1) -> datetime:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(tzinfo=None)


def _setup(make_user, db, monkeypatch, *, files_spec, download_limit=None):
    """files_spec: list of (filename, bytes, FileState). Returns
    (sender, recipient, share)."""
    sender = make_user(email="hr@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="rec@test.local", role=UserRole.client, password="Pass12345678!")

    storage_dir = tempfile.mkdtemp(prefix="fh-test-zip-")
    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings, "STORAGE_ROOT", storage_dir
    )

    share = Share(
        kind=ShareKind.outbound,
        state=ShareState.active,
        created_by_id=sender.id,
        expires_at=_naive_future(),
        download_limit=download_limit,
        downloads_remaining=download_limit,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))

    for i, (name, data, state) in enumerate(files_spec):
        abs_path = Path(storage_dir) / f"f{i}.bin"
        abs_path.write_bytes(data)
        db.add(
            File(
                id=f"00000000-0000-0000-0000-0000000000{i:02d}",
                share_id=share.id,
                original_filename=name,
                mime_type="application/octet-stream",
                size_bytes=len(data),
                storage_path=str(abs_path),
                state=state,
                uploaded_by_id=sender.id,
            )
        )
    db.commit()
    return sender, recipient, share


# ---------------------------------------------------------------- authed


@pytest.mark.asyncio
async def test_authed_zip_mint_and_consume(make_user, db, client, login_as, monkeypatch):
    _, _, share = _setup(
        make_user, db, monkeypatch,
        files_spec=[
            ("a.txt", b"alpha" * 1000, FileState.clean),
            ("b.txt", b"bravo" * 2000, FileState.clean),
            ("scanning.txt", b"nope", FileState.ready_unscanned),  # excluded
        ],
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    mint = await client.get(f"/api/files/{share.id}/download-zip-url", headers=headers)
    assert mint.status_code == 200, mint.text
    url = mint.json()["url"]
    assert url.startswith(f"/api/files/{share.id}/download-zip?dt=")

    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert "content-length" in resp.headers
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == ["a.txt", "b.txt"]  # scanning file excluded
    assert zf.testzip() is None
    assert zf.read("a.txt") == b"alpha" * 1000
    # Declared Content-Length matched the streamed body.
    assert int(resp.headers["content-length"]) == len(resp.content)


@pytest.mark.asyncio
async def test_authed_zip_decrements_budget_once(make_user, db, client, login_as, monkeypatch):
    _, _, share = _setup(
        make_user, db, monkeypatch,
        files_spec=[("a.txt", b"x" * 50, FileState.clean), ("b.txt", b"y" * 50, FileState.clean)],
        download_limit=1,
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    mint = await client.get(f"/api/files/{share.id}/download-zip-url", headers=headers)
    resp = await client.get(mint.json()["url"])
    assert resp.status_code == 200
    db.refresh(share)
    assert share.downloads_remaining == 0  # one ZIP = one decrement (not per-file)

    # Budget spent → mint refuses.
    mint2 = await client.get(f"/api/files/{share.id}/download-zip-url", headers=headers)
    assert mint2.status_code == 410
    assert mint2.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_authed_zip_no_files_400(make_user, db, client, login_as, monkeypatch):
    _, _, share = _setup(
        make_user, db, monkeypatch,
        files_spec=[("scanning.txt", b"nope", FileState.ready_unscanned)],
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{share.id}/download-zip-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "NO_DOWNLOADABLE_FILES"


# ---------------------------------------------------------------- public


def _make_link(db, sender, share, *, password=None, download_limit=None):
    created = public_link_svc.create_link(
        db, actor=sender, share=share, password=password,
        download_limit=download_limit, notify_on_download=False,
    )
    db.commit()
    return created.plaintext_token


@pytest.mark.asyncio
async def test_public_zip_no_password(make_user, db, client, monkeypatch):
    sender, _, share = _setup(
        make_user, db, monkeypatch,
        files_spec=[("a.txt", b"a" * 100, FileState.clean), ("b.txt", b"b" * 100, FileState.clean)],
    )
    token = _make_link(db, sender, share)
    resp = await client.get(f"/api/public/{token}/download-zip")
    assert resp.status_code == 200, resp.text
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == ["a.txt", "b.txt"]
    assert zf.testzip() is None


@pytest.mark.asyncio
async def test_public_zip_password_gate(make_user, db, client, monkeypatch):
    sender, _, share = _setup(
        make_user, db, monkeypatch, files_spec=[("a.txt", b"a" * 10, FileState.clean)]
    )
    token = _make_link(db, sender, share, password="s3cret!")
    resp = await client.get(f"/api/public/{token}/download-zip")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNLOCK_REQUIRED"


@pytest.mark.asyncio
async def test_public_zip_counter_exhaustion(make_user, db, client, monkeypatch):
    sender, _, share = _setup(
        make_user, db, monkeypatch, files_spec=[("a.txt", b"a" * 10, FileState.clean)],
    )
    token = _make_link(db, sender, share, download_limit=1)
    r1 = await client.get(f"/api/public/{token}/download-zip")
    assert r1.status_code == 200, r1.text
    r2 = await client.get(f"/api/public/{token}/download-zip")
    assert r2.status_code == 410
    assert r2.json()["code"] == "PUBLIC_LINK_EXHAUSTED"


@pytest.mark.asyncio
async def test_authed_zip_clips_download_log_ip_to_the_column(
    make_user, db, client, login_as, monkeypatch, app_with_db
):
    """The archive route wrote `download_log.ip` raw while the single-file
    route beside it clipped to `String(45)`. An over-long forwarded address was
    a DataError under MariaDB strict mode - after the budget decrement, so the
    download failed AND the share had paid for it. conftest's before_flush
    ratchet fails this test on the unclipped write."""
    from httpx import ASGITransport, AsyncClient

    from app.models.download_log import DownloadLog
    from app.routers.files import _DOWNLOAD_IP_MAX

    _, _, share = _setup(
        make_user, db, monkeypatch, files_spec=[("a.txt", b"a" * 10, FileState.clean)]
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    mint = await client.get(
        f"/api/files/{share.id}/download-zip-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert mint.status_code == 200, mint.text

    long_host = "2001:db8:" + ":".join(["ffff"] * 6) + "%a-scope-id-past-the-column"
    assert len(long_host) > _DOWNLOAD_IP_MAX
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(long_host, 4321)),
        base_url="http://test",
    ) as far_client:
        resp = await far_client.get(mint.json()["url"])
    assert resp.status_code == 200, resp.text

    rows = db.query(DownloadLog).filter(DownloadLog.share_id == share.id).all()
    assert rows
    assert {r.ip for r in rows} == {long_host[:_DOWNLOAD_IP_MAX]}
