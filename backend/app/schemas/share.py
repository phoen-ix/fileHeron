"""Share request / response schemas.

Phase 4: recipients are a multi-target structure with both user_ids
and group_ids. A share must have at least one of either."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from ..models.share import ShareKind, ShareState
from .common import APIBaseModel


class ShareRecipientsRequest(APIBaseModel):
    # Bounded so a single request can't submit an enormous recipient list that
    # gets fully buffered + bulk-loaded (audit L17). 1000 is far above any real
    # single-org share.
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    group_ids: list[int] = Field(default_factory=list, max_length=1000)

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _all_positive(cls, v: list[int]) -> list[int]:
        if any(i <= 0 for i in v):
            raise ValueError("ids must be positive integers")
        return v


class PublicLinkOnCreate(APIBaseModel):
    """Optional inline public-link block for `POST /api/shares`."""
    password: str | None = Field(default=None, min_length=1, max_length=255)
    download_limit: int | None = Field(default=None, gt=0, le=100_000)
    notify_on_download: bool = False


class CreateShareRequest(APIBaseModel):
    kind: ShareKind
    recipients: ShareRecipientsRequest
    # None = "never expires" (v1.1.4). Caller must pick this
    # explicitly - there's no default, so old clients that omit the
    # field still get the original 422 "field required" response.
    expires_at: datetime | None
    subject: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=4000)
    public_link: PublicLinkOnCreate | None = None
    # Whether to fan `share_created` notifications out to recipients
    # (direct user-recipients + active members of any group recipient).
    # `None` means "use the admin default kv `share.notify_recipients_default`";
    # `True`/`False` is an explicit override from the sender.
    notify_recipients: bool | None = None
    # v1.1.0 per-share download budget. None = unlimited. The counter
    # is shared across all recipients + sender + admins; first-come-
    # first-served. Mirrors the public_link.download_limit semantic.
    download_limit: int | None = Field(default=None, gt=0, le=100_000)

    @model_validator(mode="after")
    def _recipients_or_public_link(self):
        # Inbound (client → company) shares never carry recipients - the
        # audience (the whole company + group-peers) is implicit - so the
        # rule below only applies to outbound shares.
        if self.kind == ShareKind.inbound:
            return self
        # Outbound: recipients are optional iff an inline public link is
        # attached - the link IS the access mechanism. Without either, the
        # share has no path to a consumer and shouldn't exist.
        if (
            not self.recipients.user_ids
            and not self.recipients.group_ids
            and self.public_link is None
        ):
            raise ValueError(
                "At least one user/group recipient is required, "
                "or an inline public link must be attached."
            )
        return self


class UpdateShareRequest(APIBaseModel):
    """Body for `PATCH /api/shares/{id}`. All fields optional - only
    the supplied ones change.

    For the download_limit field: None means "no change" (PATCH
    semantic). To clear the limit (= make unlimited), send
    `download_limit_clear: true` instead. Splitting the unset signal
    from the no-change signal keeps the JSON shape unambiguous.

    Same shape for expires_at: omitted = no change; supplying a
    datetime replaces; supplying `expires_at_clear=true` clears the
    field (share becomes never-expire). Supplying both is a 400.
    """
    expires_at: datetime | None = None
    expires_at_clear: bool = False
    download_limit: int | None = Field(default=None, gt=0, le=100_000)
    download_limit_clear: bool = False


class FilesAddedRequest(APIBaseModel):
    """Body for `POST /api/shares/{id}/files-added` - the owner's
    batch-complete signal after uploading more files into an active share.
    `file_ids` are the freshly-uploaded file ids; `notify` opts into
    re-notifying the share's recipients."""
    notify: bool = False
    file_ids: list[str] = Field(default_factory=list, max_length=1000)


class BulkExpireRequest(APIBaseModel):
    share_ids: list[str]


class BulkExpireFailure(APIBaseModel):
    id: str
    code: str
    message: str


class BulkExpireResponse(APIBaseModel):
    expired: list[str]
    failed: list[BulkExpireFailure]


class FileInShareResponse(APIBaseModel):
    id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    state: str
    created_at: datetime
    finalized_at: datetime | None
    sha256_hex: str | None
    # True when the file is larger than clamd can scan, so it was released
    # WITHOUT a real antivirus verdict (see config.AV_MAX_SCAN_BYTES). The
    # UI surfaces this as an explicit warning rather than implying `clean`.
    av_unscanned: bool = False


class GroupRecipientRef(APIBaseModel):
    id: int
    name: str
    is_company_inbox: bool


class InlinePublicLinkResult(APIBaseModel):
    """Returned only on `POST /api/shares` when `public_link` was set
    in the request - plaintext URL shown ONCE."""
    id: str
    url: str
    qr_svg: str | None = None
    download_limit: int | None
    downloads_remaining: int | None
    notify_on_download: bool
    has_password: bool
    created_at: datetime


class ShareResponse(APIBaseModel):
    id: str
    kind: ShareKind
    state: ShareState
    subject: str | None
    # Same display-fallback rule as ShareListItem.effective_subject -
    # subject if set, else first filename, else empty string.
    effective_subject: str = ""
    message: str | None
    created_at: datetime
    # None = never-expire share (v1.1.4). SPA renders this as "Never".
    expires_at: datetime | None
    created_by_id: int
    recipient_user_ids: list[int]
    recipient_groups: list[GroupRecipientRef]
    files: list[FileInShareResponse]
    # v1.1.0 per-share download budget. Both null = unlimited.
    download_limit: int | None = None
    downloads_remaining: int | None = None
    # Populated only on creation when `public_link` was set in the
    # request body. Plaintext URL is returned ONCE here and never again.
    public_link: InlinePublicLinkResult | None = None
    # Share-approval workflow (v1.24.0). `rejection_reason` is the approver's
    # note (set when state==rejected). `viewer_can_approve` is True when the
    # current viewer may approve/reject THIS share now (approver, pending, not
    # their own) - drives the Approve/Reject buttons.
    rejection_reason: str | None = None
    approval_decided_at: datetime | None = None
    viewer_can_approve: bool = False


class RejectShareRequest(APIBaseModel):
    """Body for `POST /api/shares/{id}/reject` - optional reason shown to the
    sender."""
    reason: str | None = Field(default=None, max_length=1000)


class ShareRecipientRef(APIBaseModel):
    """Compact recipient display info, used to render group/filter UI
    without a second roundtrip per row."""
    kind: str  # 'user' or 'group'
    id: int
    label: str
    role: str | None = None  # populated for kind='user'


class ShareSenderRef(APIBaseModel):
    id: int
    display_name: str
    email: str


class ShareListItem(APIBaseModel):
    id: str
    kind: ShareKind
    state: ShareState
    subject: str | None
    # Display fallback: subject if set, else first file's filename,
    # else empty string (frontend localises to "(no subject)").
    # Computed server-side so the rule lives in one place - the
    # list endpoint doesn't expose filenames otherwise.
    effective_subject: str = ""
    created_at: datetime
    expires_at: datetime | None
    created_by_id: int
    file_count: int
    total_size_bytes: int
    # v1.1.0 per-share download budget. Both null = unlimited.
    download_limit: int | None = None
    downloads_remaining: int | None = None
    # Post-Phase 10: extra rendering data so the SPA can sort, filter,
    # and group without follow-up requests.
    recipients: list[ShareRecipientRef] = Field(default_factory=list)
    sender: ShareSenderRef | None = None


class ShareListResponse(APIBaseModel):
    items: list[ShareListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50
