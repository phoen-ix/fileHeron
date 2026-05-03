"""Upload-init + direct-upload schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import APIBaseModel


class UploadInitRequest(APIBaseModel):
    share_id: str = Field(..., min_length=36, max_length=36)
    filename: str = Field(..., min_length=1, max_length=512)
    size_bytes: int = Field(..., ge=0)
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
