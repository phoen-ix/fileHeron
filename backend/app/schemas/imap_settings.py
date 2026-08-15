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
    require_known_sender: bool
    tls_insecure: bool
    # Polling cadence/enable now lives on the Scheduled tasks page (cron 'imap_poll').
    last_poll_at: str | None
    last_success_at: str | None


class UpdateImapSettingsRequest(APIBaseModel):
    enabled: bool
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
    # Refuse mail whose From matches no enabled user. Default true - see
    # services/imap_config.require_known_sender.
    require_known_sender: bool = True
    # Do not verify the mail server's certificate. Default false; the only way
    # to set it was to write app_settings by hand, so an internal server with a
    # private CA lost inbound mail the moment TLS verification was turned on,
    # with no in-app remedy (audit #2 cross-check).
    tls_insecure: bool = False


class TestImapRequest(APIBaseModel):
    """The settings the admin currently has ON SCREEN. Sent by the Test button
    so it tests what they are about to save rather than what is stored."""

    host: str = Field(default="", max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    user: str = Field(default="", max_length=320)
    password: str | None = None
    tls_mode: str = Field(default="implicit", pattern="^(implicit|starttls|none)$")
    mailbox: str = Field(default="INBOX", max_length=255)
    # The CALLER's own password, re-confirmed. Distinct from `password` above,
    # which is the IMAP ACCOUNT's - conflating the two would be a security bug
    # with a very quiet failure mode. Required only when the request would send
    # the STORED secret to a server other than the saved one.
    confirm_password: str | None = Field(default=None, max_length=512)


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
    mailbox: str | None = None
    total: int | None = None


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
