"""Upload API: direct multipart + TUS init."""
from __future__ import annotations

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, ApiError
from fileheron_client.api import uploads as uploads_api

SERVER = "https://files.example.com"


@respx.mock
def test_upload_direct_happy(tmp_path):
    f = tmp_path / "small.bin"
    f.write_bytes(b"hello world")
    respx.post(f"{SERVER}/api/uploads/direct").mock(
        return_value=httpx.Response(
            200,
            json={"file_id": "fid-1", "size_bytes": 11, "sha256_hex": "abc"},
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    progress: list[tuple[int, int]] = []
    out = uploads_api.upload_direct(
        api,
        share_id="share-1",
        file_path=f,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert out.file_id == "fid-1"
    assert progress[-1] == (11, 11)


@respx.mock
def test_upload_direct_413_surfaces_envelope(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"X" * 16)
    respx.post(f"{SERVER}/api/uploads/direct").mock(
        return_value=httpx.Response(
            413,
            json={"code": "DIRECT_UPLOAD_TOO_LARGE", "error": "Use TUS"},
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    with pytest.raises(ApiError) as ei:
        uploads_api.upload_direct(api, share_id="share-1", file_path=f)
    assert ei.value.code == "DIRECT_UPLOAD_TOO_LARGE"


@respx.mock
def test_upload_init_returns_metadata_header():
    respx.post(f"{SERVER}/api/uploads/init").mock(
        return_value=httpx.Response(
            200,
            json={
                "file_id": "fid-2",
                "tus_endpoint": "/uploads/",
                "upload_metadata_header": "fh_payload eyJ...,fh_sig deadbeef",
                "expires_at": "2026-05-04T00:00:00",
            },
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    init = uploads_api.upload_init(
        api,
        share_id="share-1",
        filename="big.iso",
        size_bytes=10_000_000,
        mime_type="application/octet-stream",
    )
    assert init.tus_endpoint == "/uploads/"
    assert "fh_payload" in init.upload_metadata_header
