"""Admin file-history schemas (post-Phase 10)."""
from __future__ import annotations

from datetime import datetime

from .common import APIBaseModel


class FileUploaderRef(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: str


class AdminFileItem(APIBaseModel):
    file_id: str
    filename: str
    size_bytes: int
    state: str
    share_id: str
    share_subject: str | None
    share_state: str
    uploader: FileUploaderRef
    recipients_summary: str
    uploaded_at: datetime
    last_downloaded_at: datetime | None
    download_count: int


class AdminFileListResponse(APIBaseModel):
    items: list[AdminFileItem]
    total: int
    page: int
    page_size: int
