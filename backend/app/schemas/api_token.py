"""API token schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import APIBaseModel


class CreateApiTokenRequest(APIBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    # The caller's own password, re-confirmed. A valid access JWT alone used to
    # be enough to mint a token that (by default) never expires and carries no
    # scope restriction - and nothing revokes API tokens on password reset or
    # "sign out other sessions". That turned theft of a 15-minute browser token
    # into permanent access which survives the victim's obvious remediation, so
    # creation is gated the same way /change-password and /email already are.
    password: str = Field(..., min_length=1, max_length=512)
    # Optional expiry. Omit / null = never expires.
    expires_at: datetime | None = None
    # Optional least-privilege scopes. Omit / null = unrestricted (full access).
    # A non-empty list confines the token to exactly those scopes.
    scopes: list[str] | None = None


class CreateApiTokenResponse(APIBaseModel):
    """Plaintext shown ONCE - server only stores the SHA-256 hash."""
    id: int
    name: str
    last4: str
    plaintext_token: str
    created_at: datetime
    expires_at: datetime | None = None
    scopes: list[str] | None = None  # null = unrestricted
    owner_user_id: int | None = None  # populated for admin-create-on-behalf
    # Named, not just numbered: the one-time disclosure is the last chance to
    # notice the token was minted for the wrong person (audit #2).
    owner_display_name: str | None = None


class ApiTokenListItem(APIBaseModel):
    id: int
    name: str
    last4: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None = None
    scopes: list[str] | None = None  # null = unrestricted


class ApiTokenListResponse(APIBaseModel):
    items: list[ApiTokenListItem]
    # Post-Phase 10: True if the current user is allowed to mint new
    # tokens under the active policy. SPA hides the create form when False.
    can_create: bool = True


# ---------------------------------------------------------------------------
# Admin schemas (post-Phase 10)
# ---------------------------------------------------------------------------


PolicyMode = Literal["everyone", "employees_admins", "admins_only"]
TokenStatus = Literal["active", "disabled", "revoked", "expired"]


class CurrentApiTokenResponse(APIBaseModel):
    """Metadata for the API token authenticating the current request, so a
    programmatic client can show the user which token it's running on and
    where to revoke it."""
    id: int
    name: str
    last4: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None = None
    scopes: list[str] | None = None  # null = unrestricted
    status: TokenStatus


class TokenPolicyResponse(APIBaseModel):
    mode: PolicyMode
    allowed_user_ids: list[int]
    allowed_group_ids: list[int]
    # Resolved labels so the UI can show names without a second roundtrip.
    allowed_users: list[AllowedUserItem]
    allowed_groups: list[AllowedGroupItem]


class AllowedUserItem(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: str


class AllowedGroupItem(APIBaseModel):
    id: int
    name: str


class UpdateTokenPolicyRequest(APIBaseModel):
    mode: PolicyMode
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_group_ids: list[int] = Field(default_factory=list)


class AdminApiTokenItem(APIBaseModel):
    id: int
    name: str
    last4: str
    owner_user_id: int
    owner_display_name: str
    owner_email: str
    owner_role: str
    status: TokenStatus
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    disabled_at: datetime | None
    expires_at: datetime | None = None
    scopes: list[str] | None = None  # null = unrestricted


class AdminApiTokenListResponse(APIBaseModel):
    items: list[AdminApiTokenItem]
    total: int
    page: int
    page_size: int


class AdminCreateApiTokenRequest(APIBaseModel):
    target_user_id: int
    name: str = Field(..., min_length=1, max_length=120)
    # Optional expiry. Omit / null = never expires.
    expires_at: datetime | None = None
    # Optional least-privilege scopes. Omit / null = unrestricted (full access).
    scopes: list[str] | None = None


# Resolve forward refs for embedded item types.
TokenPolicyResponse.model_rebuild()
