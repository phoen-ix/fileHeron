"""TUS client: chunked PATCH, resume on 409 offset mismatch, retry."""
from __future__ import annotations

import httpx
import pytest
import respx

from fileheron_client.tus import TusError, upload_tus


SERVER = "https://files.example.com"
TUS_URL = f"{SERVER}/uploads/"
NEW_UPLOAD_URL = f"{SERVER}/uploads/new-id"


@respx.mock
def test_tus_happy_path_two_chunks(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"X" * 10_000)

    respx.post(TUS_URL).mock(
        return_value=httpx.Response(
            201, headers={"Location": "/uploads/new-id"}
        )
    )
    patch_calls: list[int] = []

    def _on_patch(request: httpx.Request) -> httpx.Response:
        offset = int(request.headers["Upload-Offset"])
        patch_calls.append(offset)
        new_offset = offset + len(request.content)
        return httpx.Response(204, headers={"Upload-Offset": str(new_offset)})

    respx.patch(NEW_UPLOAD_URL).mock(side_effect=_on_patch)

    progress: list[tuple[int, int]] = []
    final_url = upload_tus(
        server_url=SERVER,
        tus_endpoint="/uploads/",
        upload_metadata_header="fh_payload Y,fh_sig Z",
        file_path=f,
        chunk_size=4096,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert final_url == NEW_UPLOAD_URL
    assert patch_calls == [0, 4096, 8192]
    assert progress[-1] == (10_000, 10_000)


@respx.mock
def test_tus_resync_on_offset_mismatch(tmp_path):
    """If server returns 409 (offset conflict), client HEADs the URL
    and resumes from the reported offset."""
    f = tmp_path / "big.bin"
    f.write_bytes(b"X" * 8_000)

    respx.post(TUS_URL).mock(
        return_value=httpx.Response(201, headers={"Location": "/uploads/new-id"})
    )
    head_calls: list[int] = []

    def _on_head(request: httpx.Request) -> httpx.Response:
        head_calls.append(1)
        return httpx.Response(200, headers={"Upload-Offset": "0"})

    respx.head(NEW_UPLOAD_URL).mock(side_effect=_on_head)

    patch_responses = [
        # First PATCH — server says conflict.
        httpx.Response(409, text="offset mismatch"),
        # After resync, succeeds.
        httpx.Response(204, headers={"Upload-Offset": "4096"}),
        httpx.Response(204, headers={"Upload-Offset": "8000"}),
    ]
    respx.patch(NEW_UPLOAD_URL).mock(side_effect=patch_responses)

    upload_tus(
        server_url=SERVER,
        tus_endpoint="/uploads/",
        upload_metadata_header="fh_payload Y,fh_sig Z",
        file_path=f,
        chunk_size=4096,
    )
    assert len(head_calls) == 1


@respx.mock
def test_tus_relative_location_without_slash_is_resolved(tmp_path):
    """Finding C2: a Location header that is a bare relative path (no
    leading slash) must still resolve to an absolute URL for the PATCH."""
    f = tmp_path / "big.bin"
    f.write_bytes(b"X" * 4000)

    respx.post(TUS_URL).mock(
        # Note: "uploads/rel-id" — NO leading slash.
        return_value=httpx.Response(201, headers={"Location": "uploads/rel-id"})
    )
    resolved = f"{SERVER}/uploads/rel-id"

    def _on_patch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, headers={"Upload-Offset": str(len(request.content))})

    route = respx.patch(resolved).mock(side_effect=_on_patch)

    final_url = upload_tus(
        server_url=SERVER,
        tus_endpoint="/uploads/",
        upload_metadata_header="fh_payload Y,fh_sig Z",
        file_path=f,
        chunk_size=8192,
    )
    assert final_url == resolved
    assert route.called


@respx.mock
def test_tus_create_failure_raises(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"X" * 100)

    respx.post(TUS_URL).mock(
        return_value=httpx.Response(403, text="HMAC envelope invalid")
    )
    with pytest.raises(TusError) as ei:
        upload_tus(
            server_url=SERVER,
            tus_endpoint="/uploads/",
            upload_metadata_header="fh_payload Y,fh_sig Z",
            file_path=f,
        )
    assert ei.value.status_code == 403
