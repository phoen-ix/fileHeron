"""Public-link schemas (Phase 5)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import APIBaseModel


PublicLinkPolicyMode = Literal[
    "everyone", "employees_admins", "admins_only", "disabled"
]


class CreatePublicLinkRequest(APIBaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=255)
    download_limit: int | None = Field(default=None, gt=0, le=100000)
    notify_on_download: bool = False


class CreatePublicLinkResponse(APIBaseModel):
    """Returned exactly once on creation. The plaintext token + the full
    URL are not retrievable later — owner must keep them."""
    id: str
    url: str  # absolute or path-relative; depends on APP_URL config
    download_limit: int | None
    downloads_remaining: int | None
    notify_on_download: bool
    has_password: bool
    created_at: datetime


class PublicLinkResponse(APIBaseModel):
    """Metadata for the share-owner UI. ``url`` is populated for links
    created with the encrypted-token column; for legacy rows
    (created before that column shipped) it stays null and the SPA
    falls back to the "URL not stored" hint."""
    id: str
    url: str | None = None
    download_limit: int | None
    downloads_remaining: int | None
    notify_on_download: bool
    has_password: bool
    locked_until: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class PublicShareFile(APIBaseModel):
    id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    state: str


class PublicShareResponse(APIBaseModel):
    """Anonymous landing page payload at GET /d/{token}."""
    share_id: str
    subject: str | None
    message: str | None
    expires_at: datetime
    requires_password: bool
    unlocked: bool
    downloads_remaining: int | None
    files: list[PublicShareFile]


class UnlockPublicLinkRequest(APIBaseModel):
    password: str = Field(..., min_length=1, max_length=255)


class UnlockPublicLinkResponse(APIBaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Admin policy schemas (post-Phase 10)
# ---------------------------------------------------------------------------


class PublicLinkAllowedUser(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: str


class PublicLinkAllowedGroup(APIBaseModel):
    id: int
    name: str


class PublicLinkPolicyResponse(APIBaseModel):
    mode: PublicLinkPolicyMode
    allowed_user_ids: list[int]
    allowed_group_ids: list[int]
    allowed_users: list[PublicLinkAllowedUser]
    allowed_groups: list[PublicLinkAllowedGroup]


class UpdatePublicLinkPolicyRequest(APIBaseModel):
    mode: PublicLinkPolicyMode
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_group_ids: list[int] = Field(default_factory=list)
