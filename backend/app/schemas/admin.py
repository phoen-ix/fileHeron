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
    # Computed live against the 2FA policy - was a static column
    # (`users.requires_2fa_setup`) before; the column was dropped
    # because it wasn't kept consistent across policy / membership /
    # role changes.
    requires_2fa: bool
    quota_bytes: int | None
    # Authoritative storage used, summed from the DB (uploading +
    # ready_unscanned + clean files). Display-only - distinct from the fast
    # Redis quota counter used for upload enforcement, which can lapse/drift.
    storage_used_bytes: int
    created_at: datetime
    last_login_at: datetime | None
    has_2fa: bool
    # Drives the "verification pending" pill on the admin user-detail page.
    email_verified: bool = True


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
    """Admin creates a user directly - no invite, email pre-verified, with an
    admin-set password. Password floor matches every other set-password path."""
    email: EmailLike
    display_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=12, max_length=256)
    target_role: UserRole = UserRole.client
    initial_group_ids: list[int] = Field(default_factory=list)


class ForcePasswordResetResponse(APIBaseModel):
    plaintext_token: str
    expires_at: datetime


class AdminChangeEmailRequest(APIBaseModel):
    """Body for POST /api/admin/users/{id}/email."""
    new_email: EmailLike
    # Admin-only escape hatch: apply at once regardless of the configured
    # verification mode (covers "user lost access to their old mailbox").
    skip_verification: bool = False


class AdminChangeEmailResponse(APIBaseModel):
    # True ⇒ the change is already live (immediate mode / skip_verification).
    applied: bool
    mode: str
    oidc_reset: bool
    set_password_token_issued: bool
    # Plaintext confirm link(s) for a staged change, so an admin can deliver
    # them out-of-band when SMTP is unconfigured. None once applied.
    confirm_url: str | None = None
    old_confirm_url: str | None = None
    user: AdminUserItem


# --- Email-change policy settings (admin-tunable) ---------------------------

InviteState = Literal["pending", "expired"]
EmailChangeVerificationMode = Literal["immediate", "verify_new", "verify_both"]
EmailChangeOidcMode = Literal["reset_setpw", "reset_only", "keep"]


class EmailChangePolicyResponse(APIBaseModel):
    verification_mode: EmailChangeVerificationMode
    self_service: bool
    oidc_mode: EmailChangeOidcMode


class UpdateEmailChangePolicyRequest(APIBaseModel):
    verification_mode: EmailChangeVerificationMode
    self_service: bool
    oidc_mode: EmailChangeOidcMode


class EraseUserRequest(APIBaseModel):
    """Body for `POST /api/admin/users/{id}/erase`.

    Erasure is irreversible and had neither a re-auth gate nor a confirm flag,
    while the self-update routes - which are recoverable - re-prompted for the
    password. `password` is the admin's own, re-confirmed."""
    password: str = Field(..., min_length=1, max_length=512)


class EraseUserResponse(APIBaseModel):
    user_id: int
    deleted_files: int
    deleted_bytes: int
    erased_at: str
    # Names the audit row `/admin/erasure-receipts/{audit_id}/pdf` renders. The
    # receipt endpoint shipped with the feature; without this the SPA could not
    # address it (audit 2026-07-30, flow-erasure-10).
    audit_id: int | None = None
    pii_purged: dict[str, int] = Field(default_factory=dict)


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
# Mail log (v1.11.0). One row per outbound email; bodies omitted from the
# list/CSV rows (loaded only by the detail endpoint).
class AdminMailRow(APIBaseModel):
    id: int
    created_at: datetime
    recipient_email: str
    recipient_user_id: int | None
    # Hydrated server-side (bulk per page); null for non-users / erased.
    recipient_display_name: str | None = None
    category: str | None
    template_slug: str | None
    via: str
    status: str
    subject: str
    masked: bool
    attempts: int
    smtp_code: int | None
    error_class: str | None
    # Resend is disabled for masked (auth-link) rows and for test/dev rows.
    can_resend: bool


class AdminMailDetail(AdminMailRow):
    body_text: str | None
    body_html: str | None
    error_message: str | None
    source_log_id: int | None


class AdminMailListResponse(APIBaseModel):
    items: list[AdminMailRow]
    total: int
    page: int
    page_size: int
    next_cursor: str | None = None


class AdminMailResendResponse(APIBaseModel):
    ok: bool
    new_log_id: int


# ---------------------------------------------------------------------------
# Admin error log (v1.53.0). Every captured 5xx / opted-in 4xx / failed cron.
# All columns are small (no deferred bodies), so one row schema serves both the
# list and the single-row detail endpoint.
# ---------------------------------------------------------------------------


class AdminErrorRow(APIBaseModel):
    id: int
    created_at: datetime
    source: str
    status_code: int
    code: str
    exception_type: str | None
    message: str | None
    method: str | None
    path: str | None
    job_name: str | None
    ip: str | None
    request_id: str | None
    user_id: int | None
    auth_via: str | None
    signature: str
    alerted: bool


class AdminErrorListResponse(APIBaseModel):
    items: list[AdminErrorRow]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Admin session oversight (v1.7.0). A session = a `refresh_tokens` row.
# ---------------------------------------------------------------------------


class AdminSessionRow(APIBaseModel):
    id: int
    user_id: int
    # Hydrated server-side via a single bulk lookup per page (mirrors the
    # audit row). Null for erased / unknown owners.
    user_display_name: str | None = None
    user_email: str | None = None
    created_at: datetime  # session start (threaded across rotations)
    last_used_at: datetime | None = None  # latest rotation ≈ last activity
    expires_at: datetime
    revoked_at: datetime | None = None
    created_ip: str | None = None
    created_ua: str | None = None
    is_active: bool


class AdminSessionListResponse(APIBaseModel):
    items: list[AdminSessionRow]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Pending-invite admin views (post-Phase 10).
# ---------------------------------------------------------------------------


class AdminInviteItem(APIBaseModel):
    id: int
    email: str
    target_role: UserRole
    state: InviteState
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


class ErasePreflightResponse(APIBaseModel):
    """Numbers the admin sees before pressing an irreversible button.
    From `services/erasure.compute_erasure_summary`."""
    user_id: int
    display_name: str
    email: str
    role: str
    is_already_erased: bool
    files_to_delete: int
    bytes_to_delete: int
    shares_created: int
    shares_received_to_anonymize: int


class InboxUnreadCountResponse(APIBaseModel):
    unread: int


class QueuedResponse(APIBaseModel):
    """Accepted-and-enqueued acknowledgement (webhook test / delivery retry)."""
    queued: bool = True


class WebhookEventsResponse(APIBaseModel):
    events: list[str]
