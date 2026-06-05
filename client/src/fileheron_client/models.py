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
    # Admin-set default for the "notify recipients" toggle (share create +
    # add-files). Backend surfaces it on /me; default True if absent.
    share_notify_recipients_default: bool = True


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


class InlinePublicLinkResult(_Base):
    """Returned on POST /api/shares when ``public_link`` was set
    in the request — plaintext URL shown ONCE. Mirrors the backend
    schema; ``_Base`` ignores any extra fields so server-side
    additions don't break us."""
    id: str
    url: str
    download_limit: Optional[int] = None
    downloads_remaining: Optional[int] = None
    notify_on_download: bool = False
    has_password: bool = False
    created_at: Optional[datetime] = None


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
    # v0.7.1: per-share download budget for AUTHENTICATED recipients
    # (separate from + additive to the public-link's own budget).
    # None = unlimited. `downloads_remaining` is atomic-decrement
    # state, only meaningful when `download_limit` is set.
    download_limit: Optional[int] = None
    downloads_remaining: Optional[int] = None
    # v0.5.3: present only on the response to POST /api/shares when
    # the request body included ``public_link``. Pydantic would
    # silently drop the server's field if we didn't declare it,
    # which broke the "Save this URL now" popup.
    public_link: Optional[InlinePublicLinkResult] = None


class DirectUploadResponse(_Base):
    file_id: str
    size_bytes: int
    sha256_hex: str


class UploadInitResponse(_Base):
    file_id: str
    tus_endpoint: str
    upload_metadata_header: str
    expires_at: datetime


# v0.3.0 recipient-picker models -----------------------------------------------


class UserSearchItem(_Base):
    user_id: int
    display_name: str
    email: str
    role: str


class UserSearchResponse(_Base):
    items: list[UserSearchItem]


class GroupItem(_Base):
    id: int
    name: str
    description: Optional[str] = None
    is_company_inbox: bool = False
    member_count: int = 0


class GroupListResponse(_Base):
    items: list[GroupItem]
