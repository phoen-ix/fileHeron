"""S3Backend against a moto-mocked bucket (v1.22.0)."""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws
from starlette.responses import RedirectResponse

_BUCKET = "fh-test-bucket"


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setattr("app.config.settings.STORAGE_BACKEND", "s3")
    monkeypatch.setattr("app.config.settings.S3_BUCKET", _BUCKET)
    monkeypatch.setattr("app.config.settings.S3_REGION", "us-east-1")
    monkeypatch.setattr("app.config.settings.S3_ACCESS_KEY_ID", "test")
    monkeypatch.setattr("app.config.settings.S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr("app.config.settings.S3_KEY_PREFIX", "")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield


def test_s3_backend_roundtrip(s3_env, tmp_path):
    from app.services.storage_backend import S3Backend

    b = S3Backend()
    assert b.supports_disk_stats is False

    src = tmp_path / "src.part"
    src.write_bytes(b"hello s3")
    loc = b.generate_locator("fid-1")
    assert loc.endswith("fid-1.bin") and "/" in loc  # yyyy/mm/{id}.bin key
    b.finalize(str(src), loc)
    assert not src.exists()  # temp consumed after upload

    assert b.exists(loc)
    assert b.size(loc) == 8
    assert b.local_path(loc) is None  # → AV uses INSTREAM, download uses presign
    with b.open(loc) as body:
        assert body.read() == b"hello s3"

    # quarantine move (server-side copy + delete) then delete
    qloc = b.quarantine_locator("share-1", "x.txt")
    assert qloc.startswith("quarantine/share-1/")
    b.move(loc, qloc)
    assert not b.exists(loc)
    assert b.exists(qloc)
    b.delete(qloc)
    assert not b.exists(qloc)
    assert b.exists("missing/key.bin") is False


def test_s3_download_url_is_presigned(s3_env, tmp_path):
    from app.services.storage_backend import S3Backend

    b = S3Backend()
    src = tmp_path / "f.part"
    src.write_bytes(b"x")
    loc = b.generate_locator("fid-2")
    b.finalize(str(src), loc)
    url = b.download_url(locator=loc, filename="report.pdf", mime_type="application/pdf", ttl_sec=120)
    assert url and _BUCKET in url and "fid-2.bin" in url
    assert "Signature=" in url or "X-Amz-Signature=" in url  # actually presigned


def test_serve_response_redirects_for_s3(s3_env, tmp_path):
    from app.services.storage_backend import S3Backend, serve_response

    b = S3Backend()
    src = tmp_path / "f.part"
    src.write_bytes(b"x")
    loc = b.generate_locator("fid-3")
    b.finalize(str(src), loc)
    resp = serve_response(b, locator=loc, filename="x", mime_type="text/plain", ttl_sec=60)
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 307


def test_get_storage_backend_selects_s3(s3_env):
    from app.services import storage_backend as sb

    sb.reset_storage_backend_cache()
    assert sb.get_storage_backend().name == "s3"
