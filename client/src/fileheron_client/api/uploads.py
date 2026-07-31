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

from .client import ApiClient, _envelope_from_response, json_or_raise
from ..models import DirectUploadResponse, UploadInitResponse


class _ProgressReader:
    """File wrapper that reports how much has been handed to the transport.

    httpx reads the file object in chunks while streaming the multipart body,
    so counting bytes as they leave is the only way to know progress for a
    non-resumable upload. Deliberately minimal: httpx needs `read`, and `seek`
    / `tell` for length detection."""

    def __init__(self, raw, total: int, on_progress: Callable[[int, int], None]):
        self._raw = raw
        self._total = total
        self._sent = 0
        self._on_progress = on_progress

    def read(self, size: int = -1) -> bytes:
        chunk = self._raw.read(size)
        if chunk:
            self._sent += len(chunk)
            try:
                self._on_progress(min(self._sent, self._total), self._total)
            except Exception:  # a UI callback must never break the upload
                pass
        return chunk

    def seek(self, *args, **kwargs):
        # A retry rewinds the body; the counter has to rewind with it or the
        # bar would run past 100%.
        result = self._raw.seek(*args, **kwargs)
        self._sent = self._raw.tell()
        return result

    def tell(self) -> int:
        return self._raw.tell()

    def __iter__(self):
        return iter(self._raw)


def upload_direct(
    api: ApiClient,
    *,
    share_id: str,
    file_path: Path,
    mime_type: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> DirectUploadResponse:
    """One-shot multipart upload.

    ``on_progress(sent, total)`` fires as the body is consumed. It used to fire
    exactly ONCE, after the request returned, with (size, size) - so every
    direct upload (anything up to 100 MB, i.e. the common case) sat on
    "Pending" at 0% for its entire duration and then jumped straight to done.
    On a slow link that is minutes of a UI that looks stuck, next to a
    resumable upload that reports progress properly (audit 2026-07-30,
    client-4).
    """
    size = file_path.stat().st_size
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    headers["Accept"] = "application/json"
    with file_path.open("rb") as raw:
        f = _ProgressReader(raw, size, on_progress) if on_progress else raw
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
    # "error" - the dialog showed the file_id JSON as the error text.
    if resp.status_code not in (200, 201):
        raise _envelope_from_response(resp)
    if on_progress is not None:
        on_progress(size, size)
    return DirectUploadResponse.model_validate(json_or_raise(resp))


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
