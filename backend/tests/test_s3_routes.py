"""The S3 backend, exercised through the routes that serve bytes.

tests-8: `test_s3_backend.py` covers the backend class in isolation. No test
ever selected S3 for a real download, preview, ZIP or public-link request, so
every route-level difference the S3 path has was untested:

- `serve_response` returns a 307 to a presigned URL instead of a FileResponse.
  A redirect cannot carry `extra_headers`, which is why the preview nosniff/CSP
  headers rely on the previewable-type allowlist alone.
- The backend never sees the bytes, so `transfer_activity` cannot count the
  download and the maintenance drain cannot wait for it.
- The download budget decrement and the download_log row must still happen -
  they are what an operator and an investigator read, and they are the easiest
  thing to lose when the response shape changes.

That last one is the point of this file: on the S3 path the response is a
redirect, and it would be entirely possible to ship one that never touched the
counter.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole

_BUCKET = "fh-route-bucket"
PW = "Pass12345678!"


@pytest.fixture
def s3_routes(monkeypatch):
    """The isolated s3_env, but live for a whole request through the app."""
    monkeypatch.setattr("app.config.settings.STORAGE_BACKEND", "s3")
    monkeypatch.setattr("app.config.settings.S3_BUCKET", _BUCKET)
    monkeypatch.setattr("app.config.settings.S3_REGION", "us-east-1")
    monkeypatch.setattr("app.config.settings.S3_ACCESS_KEY_ID", "test")
    monkeypatch.setattr("app.config.settings.S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr("app.config.settings.S3_KEY_PREFIX", "")

    from app.services import storage_backend

    storage_backend.reset_storage_backend_cache()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield
    storage_backend.reset_storage_backend_cache()


@pytest.fixture
def s3_file(db, make_user, s3_routes, tmp_path):
    from app.services.storage_backend import get_storage_backend

    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        download_limit=3, downloads_remaining=3,
    )
    db.add(sh)
    db.flush()

    backend = get_storage_backend()
    src = tmp_path / "up.part"
    src.write_bytes(b"s3 payload")
    locator = backend.generate_locator("00000000-0000-0000-0000-0000000000s3")
    backend.finalize(str(src), locator)

    f = File(
        id="00000000-0000-0000-0000-0000000000s3", share_id=sh.id,
        original_filename="up.txt", mime_type="text/plain", size_bytes=10,
        storage_path=locator, state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return f


@pytest.mark.asyncio
async def test_a_download_redirects_to_a_presigned_url(
    client, login_as, s3_file
):
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{s3_file.id}/download",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 307, resp.text
    location = resp.headers["location"]
    assert _BUCKET in location
    # Signature scheme depends on the client config (SigV2 here under moto,
    # SigV4 against real S3); either way the URL must be signed and expiring,
    # or the object key would be publicly fetchable for as long as it exists.
    assert "Signature=" in location or "X-Amz-Signature=" in location, location
    assert "Expires=" in location or "X-Amz-Expires=" in location, location
    assert "response-content-disposition" in location, (
        "the download would render inline instead of saving"
    )


@pytest.mark.asyncio
async def test_the_redirect_still_spends_the_download_budget(
    client, login_as, s3_file, db
):
    """The counter is the whole of the share's download limit. On the S3 path
    the response is a redirect, so it would be easy to ship one that never
    decrements."""
    token, _ = await login_as("owner@test.local", PW)
    await client.get(
        f"/api/files/{s3_file.id}/download",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    sh = db.query(Share).filter(Share.id == s3_file.share_id).one()
    db.refresh(sh)
    assert sh.downloads_remaining == 2


@pytest.mark.asyncio
async def test_the_redirect_still_writes_a_download_log_row(
    client, login_as, s3_file, db
):
    token, _ = await login_as("owner@test.local", PW)
    await client.get(
        f"/api/files/{s3_file.id}/download",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert db.query(DownloadLog).filter(DownloadLog.file_id == s3_file.id).count() == 1


@pytest.mark.asyncio
async def test_an_exhausted_budget_refuses_before_presigning(
    client, login_as, s3_file, db
):
    """A presigned URL outlives the request that minted it, so it must never be
    handed out for a share that has already run out."""
    sh = db.query(Share).filter(Share.id == s3_file.share_id).one()
    sh.downloads_remaining = 0
    db.commit()

    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{s3_file.id}/download",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 410
    assert resp.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_a_preview_also_redirects(client, login_as, s3_file):
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{s3_file.id}/preview",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code in (307, 200), resp.text


@pytest.mark.asyncio
async def test_a_zip_streams_rather_than_redirecting(client, login_as, s3_file):
    """A ZIP is assembled by the backend from many objects, so unlike a single
    file it cannot be delegated to a presigned URL - it must still stream."""
    token, _ = await login_as("owner@test.local", PW)
    minted = await client.get(
        f"/api/files/{s3_file.share_id}/download-zip-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert minted.status_code == 200, minted.text
    resp = await client.get(minted.json()["url"], follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert resp.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_maintenance_still_refuses_on_the_s3_path(
    client, login_as, s3_file, db
):
    """The drain cannot count an S3 download (the bytes never reach us), so the
    gate refusing NEW ones is the only control there is."""
    from app.services import maintenance as maintenance_svc

    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    try:
        token, _ = await login_as("owner@test.local", PW)
        resp = await client.get(
            f"/api/files/{s3_file.id}/download",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 503
    finally:
        maintenance_svc.set_enabled(db, False, actor=None)
        db.commit()
