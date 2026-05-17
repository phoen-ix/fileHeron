"""Upload endpoints.

Two paths:
- ``upload_direct``: single multipart POST to ``/api/uploads/direct``
  (≤ ``MAX_DIRECT_UPLOAD_BYTES``, default 100 MB on the server).
- ``upload_init``: returns a TUS upload URL + pre-built
  ``Upload-Metadata`` header. The actual chunked PATCH lives in
  ``fileheron_client.tus``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .client import ApiClient, _envelope_from_response
from ..models import DirectUploadResponse, UploadInitResponse


def upload_direct(
    api: ApiClient,
    *,
    share_id: str,
    file_path: Path,
    mime_type: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> DirectUploadResponse:
    """One-shot multipart upload. ``on_progress`` is called by httpx
    via the chunked send; for very small files it may fire once.
    Caller should keep file_path open for the duration of this call.
    """
    size = file_path.stat().st_size
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    headers["Accept"] = "application/json"
    with file_path.open("rb") as f:
        files = {
            "file": (
                file_path.name,
                f,
                mime_type or "application/octet-stream",
            )
        }
        data = {"share_id": share_id}
        resp = api._http.post(
            "/api/uploads/direct",
            data=data,
            files=files,
            headers=headers,
        )
    # v0.5.2: backend returns 201 Created on success (uploads.py is a
    # creation endpoint, status_code=HTTP_201_CREATED). Treating only
    # 200 as success made the success body get raised as a fake
    # "error" — the dialog showed the file_id JSON as the error text.
    if resp.status_code not in (200, 201):
        raise _envelope_from_response(resp)
    if on_progress is not None:
        on_progress(size, size)
    return DirectUploadResponse.model_validate(resp.json())


def upload_init(
    api: ApiClient,
    *,
    share_id: str,
    filename: str,
    size_bytes: int,
    mime_type: str = "application/octet-stream",
) -> UploadInitResponse:
    body = {
        "share_id": share_id,
        "filename": filename,
        "size_bytes": size_bytes,
        "mime_type": mime_type,
    }
    out = api.request_or_raise("POST", "/api/uploads/init", json=body)
    return UploadInitResponse.model_validate(out)
