"""Subset Pydantic mirrors of the backend response schemas.

We don't try to reproduce the whole backend type system — only the
fields the client actually reads. ``model_config`` allows extra
fields so future server additions don't break the client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LoginResponse(_Base):
    access_token: str
    expires_in_seconds: int


class RefreshResponse(_Base):
    access_token: str
    expires_in_seconds: int


class MeResponse(_Base):
    id: int
    email: str
    display_name: str
    role: str
    locale: str
    quota_bytes: Optional[int] = None
    requires_2fa: bool = False
    can_create_public_link: bool = True


class ShareSenderRef(_Base):
    id: int
    display_name: str
    email: str


class ShareRecipientRef(_Base):
    kind: str
    id: int
    label: str
    role: Optional[str] = None


class ShareListItem(_Base):
    id: str
    kind: str
    state: str
    subject: Optional[str] = None
    effective_subject: str = ""
    created_at: datetime
    expires_at: Optional[datetime] = None
    created_by_id: int
    file_count: int
    total_size_bytes: int
    recipients: list[ShareRecipientRef] = []
    sender: Optional[ShareSenderRef] = None


class ShareListResponse(_Base):
    items: list[ShareListItem]
    total: int
    page: int
    page_size: int


class FileInShareResponse(_Base):
    id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    state: str
    created_at: datetime
    finalized_at: Optional[datetime] = None
    sha256_hex: Optional[str] = None


class GroupRecipientRef(_Base):
    id: int
    name: str
    is_company_inbox: bool = False


class ShareResponse(_Base):
    id: str
    kind: str
    state: str
    subject: Optional[str] = None
    effective_subject: str = ""
    message: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    created_by_id: int
    recipient_user_ids: list[int] = []
    recipient_groups: list[GroupRecipientRef] = []
    files: list[FileInShareResponse] = []


class DirectUploadResponse(_Base):
    file_id: str
    size_bytes: int
    sha256_hex: str


class UploadInitResponse(_Base):
    file_id: str
    tus_endpoint: str
    upload_metadata_header: str
    expires_at: datetime
