"""Admin-only schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..models.user import Locale, UserRole
from .common import APIBaseModel
from .types import EmailLike


class AdminUserItem(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: UserRole
    is_disabled: bool
    # Computed live against the 2FA policy — was a static column
    # (`users.requires_2fa_setup`) before; the column was dropped
    # because it wasn't kept consistent across policy / membership /
    # role changes.
    requires_2fa: bool
    quota_bytes: int | None
    # Live Redis quota counter (kept honest by the hourly
    # workers/quota_reconcile.py cron). Useful for spotting "who's
    # eating disk" on the /admin/users list without drilling into
    # the file-history view.
    storage_used_bytes: int
    created_at: datetime
    last_login_at: datetime | None
    has_2fa: bool


class AdminUserListResponse(APIBaseModel):
    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int


class UpdateUserRequest(APIBaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    quota_bytes: int | None = Field(default=None, ge=0)
    is_disabled: bool | None = None


class CreateUserRequest(APIBaseModel):
    """Admin creates a user directly — no invite, email pre-verified, with an
    admin-set password. Password floor matches every other set-password path."""
    email: EmailLike
    display_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=12, max_length=256)
    target_role: UserRole = UserRole.client
    initial_group_ids: list[int] = Field(default_factory=list)


class ForcePasswordResetResponse(APIBaseModel):
    plaintext_token: str
    expires_at: datetime


class EraseUserResponse(APIBaseModel):
    user_id: int
    deleted_files: int
    deleted_bytes: int
    erased_at: str


class AdminAuditRow(APIBaseModel):
    id: int
    event_type: str
    actor_user_id: int | None
    # Hydrated server-side via a single bulk lookup per page so the
    # SPA can show "Alice (a***@example.com)" instead of bare integer
    # IDs. Both fields stay null for system / anonymous events AND for
    # actors whose accounts have been erased (the `users` row is gone).
    actor_display_name: str | None = None
    actor_email: str | None = None
    target_type: str | None
    target_id: str | None
    request_id: str | None
    ip: str | None
    extra: dict | None
    created_at: datetime


class AdminAuditResponse(APIBaseModel):
    items: list[AdminAuditRow]
    # Legacy offset fields. Meaningful only when the caller used
    # ?page=… (no cursor). When the caller follows a `next_cursor`
    # link, `total` reflects only the matching count above the cursor
    # and `page` is 1.
    total: int
    page: int
    page_size: int
    # Opaque cursor for the next-older page. Null when the current page
    # is the last one in the result set.
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Pending-invite admin views (post-Phase 10).
# ---------------------------------------------------------------------------


class AdminInviteItem(APIBaseModel):
    id: int
    email: str
    target_role: UserRole
    state: Literal["pending", "expired"]
    invited_by_id: int | None
    # Hydrated by the router via a single bulk lookup per page so the
    # SPA can show "Alice" instead of bare integer IDs. None when the
    # inviter has been erased.
    invited_by_display_name: str | None = None
    initial_group_ids: list[int] | None
    created_at: datetime
    expires_at: datetime


class AdminInviteListResponse(APIBaseModel):
    items: list[AdminInviteItem]
    total: int
    page: int
    page_size: int


class ActivateInviteRequest(APIBaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    locale: Locale | None = None


class RegenerateInviteResponse(APIBaseModel):
    # Plaintext token, returned exactly once. Caller copies to clipboard.
    token: str
    # Full /register/<token> URL using the current site URL.
    url: str
    expires_at: datetime


class ResendInviteResponse(APIBaseModel):
    ok: bool
    expires_at: datetime
