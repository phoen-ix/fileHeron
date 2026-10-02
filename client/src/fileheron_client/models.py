"""Subset Pydantic mirrors of the backend response schemas.

We don't try to reproduce the whole backend type system - only the
fields the client actually reads. ``model_config`` allows extra
fields so future server additions don't break the client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LoginResponse(_Base):
    access_token: str
    expires_in_seconds: int


class RefreshResponse(_Base):
    access_token: str
    expires_in_seconds: int


class SecretLimitsResponse(_Base):
    """The admin's ceilings, for the compose forms."""
    max_views: int
    max_expiry_days: int
    max_lifetime_days: int
    passphrase_failure_mode: str
    passphrase_max_failures: int


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
    # Secrets (server v2.24.0). An older server sends none of these, so the
    # defaults hide the Secrets tab rather than offering routes it lacks.
    # `secrets_enabled` shows the tab (anyone may RECEIVE a secret);
    # `can_send_secrets` the New secret / Request a secret buttons;
    # `can_send_secrets_external` the address + link options.
    secrets_enabled: bool = False
    can_send_secrets: bool = False
    can_send_secrets_external: bool = False
    secret_limits: Optional[SecretLimitsResponse] = None


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
    # True when the file was released WITHOUT a real antivirus verdict because
    # it is larger than clamd can scan. `state` is still "clean" - that is what
    # keeps it downloadable - so this flag is the ONLY thing distinguishing
    # "scanned and clean" from "never scanned". The backend has sent it since
    # v2.4.0 and the web UI warns on it; this client silently dropped it, so
    # desktop users were the only ones who could not tell the difference.
    av_unscanned: bool = False
    # "pending_review" while a file added to an already-approved share waits for
    # its own four-eyes decision (server v2.9.0). Only the owner and approvers
    # ever receive such a row; recipients get a 409 on the bytes.
    approval_state: str = "approved"


class GroupRecipientRef(_Base):
    id: int
    name: str
    is_company_inbox: bool = False


class InlinePublicLinkResult(_Base):
    """Returned on POST /api/shares when ``public_link`` was set
    in the request. The URL stays readable afterwards via
    GET /api/shares/{id}/public-link (owner and admins). Mirrors the backend
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
    # `str | None` server-side. Declaring it required here would reject a
    # successful upload's reply after the bytes had landed - an "upload
    # failed" for a file the server already holds.
    sha256_hex: Optional[str] = None


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


# Secrets + secret requests (server v2.24.0) ------------------------------------
#
# Metadata only, except `RevealSecretResponse.content` - the one place a
# secret's text ever reaches the client. Nothing here is logged.


class SecretUserRef(_Base):
    id: int
    display_name: str


class SecretGroupRef(_Base):
    id: int
    name: str


class SecretRecipientSummary(_Base):
    users: int = 0
    groups: int = 0
    emails: int = 0
    link: bool = False


class SecretListItem(_Base):
    id: str
    state: str
    label: Optional[str] = None
    # None for an answer written without an account (then `answered_via` and
    # `answered_by_email` say where it came from).
    sender: Optional[SecretUserRef] = None
    created_at: datetime
    ended_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = None
    view_scope: str = "per_person"
    # Sent box only; None in the received box.
    views_used: Optional[int] = None
    has_passphrase: bool = False
    my_views_left: Optional[int] = None
    recipient_summary: Optional[SecretRecipientSummary] = None
    is_answer: bool = False
    answered_via: Optional[str] = None
    answered_by_email: Optional[str] = None


class SecretListResponse(_Base):
    items: list[SecretListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50


class SecretMemberStatus(_Base):
    user: SecretUserRef
    views_used: int = 0
    last_viewed_at: Optional[datetime] = None
    eligible: bool = True
    burned: bool = False


class SecretRecipientStatus(_Base):
    id: int
    kind: str
    user: Optional[SecretUserRef] = None
    group: Optional[SecretGroupRef] = None
    email: Optional[str] = None
    views_used: int = 0
    views_left: Optional[int] = None
    failed_attempts: int = 0
    locked_until: Optional[datetime] = None
    burned: bool = False
    revoked: bool = False
    emailed_at: Optional[datetime] = None
    members: list[SecretMemberStatus] = []


class SecretEvent(_Base):
    at: datetime
    outcome: str
    kind: Optional[str] = None
    user: Optional[SecretUserRef] = None
    email: Optional[str] = None
    ip: Optional[str] = None


class SecretResponse(_Base):
    id: str
    state: str
    label: Optional[str] = None
    sender: Optional[SecretUserRef] = None
    created_at: datetime
    ended_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = None
    view_scope: str = "per_person"
    views_used: Optional[int] = None
    has_passphrase: bool = False
    # The requester's own passphrase layer (an answer to a request only).
    has_request_passphrase: bool = False
    notify_on_view: bool = False
    burn_after_failures: Optional[int] = None
    viewer_role: str = "recipient"
    is_answer: bool = False
    request_id: Optional[str] = None
    answered_via: Optional[str] = None
    answered_by_email: Optional[str] = None
    can_burn: bool = False
    my_views_left: Optional[int] = None
    can_reveal: bool = False
    still_recipient: bool = False
    burned_for_me: bool = False
    my_failed_attempts: int = 0
    recipients: list[SecretRecipientStatus] = []
    recipient_summary: SecretRecipientSummary = Field(default_factory=SecretRecipientSummary)
    events: list[SecretEvent] = []
    # The create response only, when a link was made.
    link_url: Optional[str] = None


class RevealSecretResponse(_Base):
    content: str
    views_left: Optional[int] = None
    # True when this view was the last one anybody had.
    ended: bool = False


class SecretLinkItem(_Base):
    recipient_id: int
    kind: str
    email: Optional[str] = None
    # None when the link can no longer be shown.
    url: Optional[str] = None


class SecretLinksResponse(_Base):
    items: list[SecretLinkItem]


class SecretRequestTargetStatus(_Base):
    id: int
    kind: str
    user: Optional[SecretUserRef] = None
    group: Optional[SecretGroupRef] = None
    email: Optional[str] = None
    notified_at: Optional[datetime] = None


class SecretRequestResponse(_Base):
    id: str
    state: str
    closed_reason: Optional[str] = None
    label: str
    note: Optional[str] = None
    requester: SecretUserRef
    created_at: datetime
    expires_at: datetime
    ended_at: Optional[datetime] = None
    answer_max_views: Optional[int] = None
    answer_expires_in_sec: Optional[int] = None
    has_passphrase: bool = False
    viewer_role: str = "target"
    can_answer: bool = False
    targets: list[SecretRequestTargetStatus] = []
    target_summary: SecretRecipientSummary = Field(default_factory=SecretRecipientSummary)
    fulfilled_at: Optional[datetime] = None
    answered_via: Optional[str] = None
    answered_by: Optional[SecretUserRef] = None
    answered_by_email: Optional[str] = None
    answer_secret_id: Optional[str] = None
    # The create response only, when a link was made.
    link_url: Optional[str] = None


class SecretRequestListItem(_Base):
    id: str
    state: str
    closed_reason: Optional[str] = None
    label: str
    requester: SecretUserRef
    created_at: datetime
    expires_at: datetime
    ended_at: Optional[datetime] = None
    has_passphrase: bool = False
    target_summary: Optional[SecretRecipientSummary] = None
    answer_secret_id: Optional[str] = None
    can_answer: bool = False


class SecretRequestListResponse(_Base):
    items: list[SecretRequestListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50


class AnswerSecretRequestResponse(_Base):
    ok: bool
    requester_name: Optional[str] = None


class SecretRequestLinkItem(_Base):
    target_id: int
    kind: str
    email: Optional[str] = None
    url: Optional[str] = None


class SecretRequestLinksResponse(_Base):
    items: list[SecretRequestLinkItem]
