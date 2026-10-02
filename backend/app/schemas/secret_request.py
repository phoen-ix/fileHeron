"""Secret request schemas (v2.24.0): asking someone for a secret.

No model here carries a secret: the answer's text goes IN through
`AnswerSecretRequestRequest` / `PublicAnswerSecretRequestRequest` and comes back
out only through the ordinary secret reveal (schemas/secret.py). The label and
note are what the requester chose to show whoever may answer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..models.secret import SecretRecipientKind
from ..models.secret_request import SecretRequestState
from .common import APIBaseModel
from .secret import (
    PublicSecretTokenRequest,
    SecretGroupRef,
    SecretRecipientsRequest,
    SecretRecipientSummary,
    SecretUserRef,
)

AnsweredVia = Literal["user", "email", "link"]


class CreateSecretRequestRequest(APIBaseModel):
    # What is asked for - shown to everyone who may answer, link holders too.
    label: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    # Open until. The admin ceiling (max expiry) is checked by the service.
    expires_at: datetime
    # How the answer may be read: at least one (SECRET_NEEDS_LIMIT). The
    # lifetime counts from the moment the answer arrives.
    answer_max_views: int | None = Field(default=None, ge=1, le=10_000)
    answer_expires_in_sec: int | None = Field(default=None, ge=60, le=3650 * 86400)
    # The requester's own passphrase: part of the answer's encryption, never
    # stored, needed to open the answer - the form says it cannot be recovered.
    passphrase: str | None = Field(default=None, min_length=8, max_length=256)
    recipients: SecretRecipientsRequest = Field(default_factory=SecretRecipientsRequest)
    create_link: bool = False


class SecretRequestTargetStatus(APIBaseModel):
    id: int
    kind: SecretRecipientKind
    user: SecretUserRef | None = None
    group: SecretGroupRef | None = None
    email: str | None = None
    notified_at: datetime | None = None


class SecretRequestResponse(APIBaseModel):
    id: str
    state: SecretRequestState
    # Why it takes no answer ("fulfilled", "cancelled", "expired" - the last
    # also while the sweep has not caught up), or None while open.
    closed_reason: str | None
    label: str
    note: str | None
    requester: SecretUserRef
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    answer_max_views: int | None
    answer_expires_in_sec: int | None
    # The requester set a passphrase: the answer opens only with it.
    has_passphrase: bool
    viewer_role: Literal["requester", "target", "admin"]
    # A target that may still answer.
    can_answer: bool = False
    # Requester and admin: whom it went to, and how it was answered.
    targets: list[SecretRequestTargetStatus] = Field(default_factory=list)
    target_summary: SecretRecipientSummary = Field(default_factory=SecretRecipientSummary)
    fulfilled_at: datetime | None = None
    answered_via: AnsweredVia | None = None
    answered_by: SecretUserRef | None = None
    # The requester alone: the address that answered, and the answer to open.
    answered_by_email: str | None = None
    answer_secret_id: str | None = None
    # Set on the create response only, when a link was made.
    link_url: str | None = None
    link_qr_svg: str | None = None


class SecretRequestListItem(APIBaseModel):
    id: str
    state: SecretRequestState
    closed_reason: str | None
    label: str
    requester: SecretUserRef
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    has_passphrase: bool
    # "mine" box: whom it went to, and the answer. "asked" box: can I answer.
    target_summary: SecretRecipientSummary | None = None
    answer_secret_id: str | None = None
    can_answer: bool = False


class SecretRequestListResponse(APIBaseModel):
    items: list[SecretRequestListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50


class AnswerSecretRequestRequest(APIBaseModel):
    # Never stripped: whitespace can be part of a secret.
    content: str = Field(min_length=1, max_length=10_000)
    # The answerer's own optional passphrase, on top of the requester's.
    passphrase: str | None = Field(default=None, min_length=8, max_length=256)


class AnswerSecretRequestResponse(APIBaseModel):
    ok: bool
    requester_name: str | None


class PublicAnswerSecretRequestRequest(PublicSecretTokenRequest):
    content: str = Field(min_length=1, max_length=10_000)
    passphrase: str | None = Field(default=None, min_length=8, max_length=256)


class PublicSecretRequestPeekResponse(APIBaseModel):
    requester_name: str | None
    label: str
    note: str | None
    expires_at: datetime
    answer_max_views: int | None
    answer_expires_in_sec: int | None
    has_passphrase: bool


class SecretRequestLinkItem(APIBaseModel):
    target_id: int
    kind: SecretRecipientKind
    email: str | None
    # None once the request is closed (or undecryptable after a key rotation).
    url: str | None
    qr_svg: str | None


class SecretRequestLinksResponse(APIBaseModel):
    items: list[SecretRequestLinkItem]


class AdminSecretRequestListItem(SecretRequestListItem):
    requester_email: str


class AdminSecretRequestListResponse(APIBaseModel):
    items: list[AdminSecretRequestListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50
