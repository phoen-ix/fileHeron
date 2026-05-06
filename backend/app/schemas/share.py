"""Share request / response schemas.

Phase 4: recipients are a multi-target structure with both user_ids
and group_ids. A share must have at least one of either."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from ..models.share import ShareKind, ShareState
from .common import APIBaseModel


class ShareRecipientsRequest(APIBaseModel):
    user_ids: list[int] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _all_positive(cls, v: list[int]) -> list[int]:
        if any(i <= 0 for i in v):
            raise ValueError("ids must be positive integers")
        return v


class PublicLinkOnCreate(APIBaseModel):
    """Optional inline public-link block for `POST /api/shares`."""
    password: str | None = Field(default=None, min_length=1, max_length=255)
    download_limit: int | None = Field(default=None, gt=0, le=100000)
    notify_on_download: bool = False


class CreateShareRequest(APIBaseModel):
    kind: ShareKind
    recipients: ShareRecipientsRequest
    expires_at: datetime
    subject: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=4000)
    public_link: PublicLinkOnCreate | None = None
    # Whether to fan `share_created` notifications out to recipients
    # (direct user-recipients + active members of any group recipient).
    # `None` means "use the admin default kv `share.notify_recipients_default`";
    # `True`/`False` is an explicit override from the sender.
    notify_recipients: bool | None = None

    @model_validator(mode="after")
    def _recipients_or_public_link(self):
        # Recipients are optional iff an inline public link is attached
        # — the link IS the access mechanism. Without either, the share
        # has no path to a consumer and shouldn't exist.
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
    """Body for `PATCH /api/shares/{id}`. Currently only `expires_at` is
    editable; the schema is in its own model so future fields slot in
    without breaking callers."""
    expires_at: datetime


class FileInShareResponse(APIBaseModel):
    id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    state: str
    created_at: datetime
    finalized_at: datetime | None
    sha256_hex: str | None


class GroupRecipientRef(APIBaseModel):
    id: int
    name: str
    is_company_inbox: bool


class InlinePublicLinkResult(APIBaseModel):
    """Returned only on `POST /api/shares` when `public_link` was set
    in the request — plaintext URL shown ONCE."""
    id: str
    url: str
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
    # Same display-fallback rule as ShareListItem.effective_subject —
    # subject if set, else first filename, else empty string.
    effective_subject: str = ""
    message: str | None
    created_at: datetime
    expires_at: datetime
    created_by_id: int
    recipient_user_ids: list[int]
    recipient_groups: list[GroupRecipientRef]
    files: list[FileInShareResponse]
    # Populated only on creation when `public_link` was set in the
    # request body. Plaintext URL is returned ONCE here and never again.
    public_link: InlinePublicLinkResult | None = None


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
    # Computed server-side so the rule lives in one place — the
    # list endpoint doesn't expose filenames otherwise.
    effective_subject: str = ""
    created_at: datetime
    expires_at: datetime
    created_by_id: int
    file_count: int
    total_size_bytes: int
    # Post-Phase 10: extra rendering data so the SPA can sort, filter,
    # and group without follow-up requests.
    recipients: list[ShareRecipientRef] = Field(default_factory=list)
    sender: ShareSenderRef | None = None


class ShareListResponse(APIBaseModel):
    items: list[ShareListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50
