"""Upload-init + direct-upload schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import APIBaseModel

# Ceiling on the size an upload may DECLARE. Not the product limit (README
# documents ~30 GB) - two orders of magnitude above it, because the number
# lands straight in a `files` row in state `uploading` and that state counts
# toward both the user's quota and the nightly analytics storage snapshot. An
# unbounded declaration let any account inflate a snapshot that is written once
# per date and never recomputed, without uploading a byte (audit 2026-07-30).
MAX_DECLARED_UPLOAD_BYTES = 1024**4  # 1 TiB


class UploadInitRequest(APIBaseModel):
    share_id: str = Field(..., min_length=36, max_length=36)
    filename: str = Field(..., min_length=1, max_length=512)
    size_bytes: int = Field(..., ge=0, le=MAX_DECLARED_UPLOAD_BYTES)
    mime_type: str = Field(default="application/octet-stream", max_length=255)


class UploadInitResponse(APIBaseModel):
    """Returned by POST /api/uploads/init.

    The TUS client (Uppy / tuspy / curl) creates the upload at
    `tus_endpoint` and includes `upload_metadata_header` as the value of
    `Upload-Metadata` on every chunk + the initial POST. The envelope rides
    inside that header to authorize this upload."""
    file_id: str
    tus_endpoint: str
    upload_metadata_header: str
    expires_at: datetime


class DirectUploadResponse(APIBaseModel):
    """Returned by POST /api/uploads/direct (multipart/form-data path)."""
    file_id: str
    size_bytes: int
    sha256_hex: str | None
