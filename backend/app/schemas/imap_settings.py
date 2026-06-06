"""Admin IMAP / inbound-mailbox schemas (v1.27.0)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import APIBaseModel

PostFetchAction = ("mark_read", "untouched", "move", "delete")
NotifyMode = ("off", "human", "all")
TlsMode = ("implicit", "starttls", "none")
CheckMode = ("auto", "manual")


class ImapSettingsResponse(APIBaseModel):
    enabled: bool
    check_mode: str
    use_smtp_credentials: bool
    host: str
    port: int
    user: str
    is_password_set: bool
    tls_mode: str
    mailbox: str
    post_fetch_action: str
    move_folder: str
    notify_mode: str
    poll_interval_minutes: int
    last_poll_at: str | None
    last_success_at: str | None


class UpdateImapSettingsRequest(APIBaseModel):
    enabled: bool
    check_mode: str = Field(pattern="^(auto|manual)$")
    # When true, IMAP reuses the SMTP login; user/password below are ignored.
    use_smtp_credentials: bool = True
    host: str = Field(default="", max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    user: str = Field(default="", max_length=320)
    # null = keep existing, "" = clear, other = replace (mirrors SMTP).
    password: str | None = None
    tls_mode: str = Field(pattern="^(implicit|starttls|none)$")
    mailbox: str = Field(default="INBOX", max_length=255)
    post_fetch_action: str = Field(pattern="^(mark_read|untouched|move|delete)$")
    move_folder: str = Field(default="fileHeron/Processed", max_length=255)
    notify_mode: str = Field(pattern="^(off|human|all)$")
    poll_interval_minutes: int = Field(default=5, ge=1, le=1440)


class ImapTestResponse(APIBaseModel):
    ok: bool
    error: str | None = None
    hint: str | None = None
    folders: list[str] = Field(default_factory=list)


class ImapFetchNowResponse(APIBaseModel):
    ok: bool
    skipped: str | None = None
    error: str | None = None
    fetched: int | None = None
    ingested: int | None = None


# ---- Inbox -----------------------------------------------------------------


class InboxAttachmentItem(APIBaseModel):
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    av_state: str


class InboxListItem(APIBaseModel):
    id: int
    created_at: datetime
    received_at: datetime | None
    sender_email: str
    sender_name: str | None
    sender_user_id: int | None
    subject: str
    classification: str
    status: str
    has_attachments: bool


class InboxListResponse(APIBaseModel):
    items: list[InboxListItem]
    total: int
    page: int
    page_size: int
    unread: int


class InboxDetail(InboxListItem):
    to_addr: str | None
    message_id: str | None
    in_reply_to: str | None
    body_text: str | None
    body_html: str | None
    attachments: list[InboxAttachmentItem]


class UpdateInboxStatusRequest(APIBaseModel):
    status: str = Field(pattern="^(new|read|archived)$")
