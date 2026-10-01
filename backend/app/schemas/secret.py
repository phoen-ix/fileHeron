"""Secret request / response schemas (v2.24.0).

The content appears in exactly one response model, `RevealSecretResponse`.
Every other model here is metadata, and `response_model` filtering means a key
missing from a model is deleted from the wire - so each model is written from
what its handler actually returns.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ..models.secret import (
    SecretAccessOutcome,
    SecretRecipientKind,
    SecretState,
    SecretViewScope,
)
from .common import APIBaseModel
from .types import EmailLike

SecretPolicyMode = Literal["everyone", "employees_admins", "admins_only"]
PassphraseFailureMode = Literal["lock", "burn"]


class SecretRecipientsRequest(APIBaseModel):
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    group_ids: list[int] = Field(default_factory=list, max_length=1000)
    # Addresses with no account; each gets its own link by email. Bounded low:
    # each one is an outbound mail to an arbitrary address.
    emails: list[EmailLike] = Field(default_factory=list, max_length=20)

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _all_positive(cls, v: list[int]) -> list[int]:
        if any(i <= 0 for i in v):
            raise ValueError("ids must be positive integers")
        return v

    @field_validator("emails")
    @classmethod
    def _dedupe_emails(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class CreateSecretRequest(APIBaseModel):
    # Never stripped: whitespace can be part of a secret.
    content: str = Field(min_length=1, max_length=10_000)
    label: str | None = Field(default=None, max_length=200)
    # Part of the encryption key, so it cannot be recovered - the form says so.
    passphrase: str | None = Field(default=None, min_length=8, max_length=256)
    # At least one of max_views / expires_at (SECRET_NEEDS_LIMIT). The admin
    # ceilings are checked by the service, which knows them.
    max_views: int | None = Field(default=None, ge=1, le=10_000)
    view_scope: SecretViewScope = SecretViewScope.per_person
    expires_at: datetime | None = None
    recipients: SecretRecipientsRequest = Field(default_factory=SecretRecipientsRequest)
    create_link: bool = False
    notify_on_view: bool = False
    # Burn after N wrong passphrases for this secret, where the admin default is
    # throttle + lock. A sender can tighten the rule, never loosen it.
    burn_on_failures: bool = False


class SecretUserRef(APIBaseModel):
    id: int
    display_name: str


class SecretGroupRef(APIBaseModel):
    id: int
    name: str


class SecretMemberStatus(APIBaseModel):
    user: SecretUserRef
    views_used: int
    last_viewed_at: datetime | None
    # False once they left the group (or were disabled): they can no longer
    # read it.
    eligible: bool
    burned: bool


class SecretRecipientStatus(APIBaseModel):
    id: int
    kind: SecretRecipientKind
    user: SecretUserRef | None = None
    group: SecretGroupRef | None = None
    email: str | None = None
    views_used: int
    views_left: int | None
    failed_attempts: int
    locked_until: datetime | None
    burned: bool
    revoked: bool
    emailed_at: datetime | None
    created_at: datetime
    # A group's members, with their own counts (sender/admin only).
    members: list[SecretMemberStatus] = Field(default_factory=list)


class SecretEvent(APIBaseModel):
    at: datetime
    outcome: SecretAccessOutcome
    recipient_id: int | None
    kind: SecretRecipientKind | None
    user: SecretUserRef | None
    email: str | None
    # The only identity an address/link view has. An account view's address is
    # shown to admins only.
    ip: str | None


class SecretRecipientSummary(APIBaseModel):
    users: int = 0
    groups: int = 0
    emails: int = 0
    link: bool = False


class SecretResponse(APIBaseModel):
    id: str
    state: SecretState
    label: str | None
    sender: SecretUserRef
    created_at: datetime
    ended_at: datetime | None
    expires_at: datetime | None
    max_views: int | None
    view_scope: SecretViewScope
    # Every view, by anyone: the sender's and an admin's to know. A recipient
    # gets None - it would tell them how often the others looked - and reads
    # `my_views_left` instead.
    views_used: int | None
    has_passphrase: bool
    notify_on_view: bool
    burn_after_failures: int | None
    viewer_role: Literal["sender", "admin", "recipient"]
    # The viewing recipient's own standing (recipient view only).
    my_views_left: int | None = None
    can_reveal: bool = False
    still_recipient: bool = False
    burned_for_me: bool = False
    my_failed_attempts: int = 0
    # The full roster and view log (sender and admin only).
    recipients: list[SecretRecipientStatus] = Field(default_factory=list)
    recipient_summary: SecretRecipientSummary = Field(default_factory=SecretRecipientSummary)
    events: list[SecretEvent] = Field(default_factory=list)
    # Set on the create response only, when a link was made: the one time the
    # API hands it over unasked.
    link_url: str | None = None
    link_qr_svg: str | None = None


class SecretListItem(APIBaseModel):
    id: str
    state: SecretState
    label: str | None
    sender: SecretUserRef
    created_at: datetime
    ended_at: datetime | None
    expires_at: datetime | None
    max_views: int | None
    view_scope: SecretViewScope
    # Sent box (and the admin list) only; None in the received box, like
    # SecretResponse.views_used.
    views_used: int | None
    has_passphrase: bool
    # Received box: what the viewer may still view. Sent box: whom it went to.
    my_views_left: int | None = None
    recipient_summary: SecretRecipientSummary | None = None


class SecretListResponse(APIBaseModel):
    items: list[SecretListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50


class RevealSecretRequest(APIBaseModel):
    passphrase: str | None = Field(default=None, max_length=256)


class RevealSecretResponse(APIBaseModel):
    content: str
    views_left: int | None
    # True when this view was the last one anybody had: the secret is gone now.
    ended: bool


class PublicSecretTokenRequest(APIBaseModel):
    # In the BODY, never the URL: the link carries it in the fragment.
    token: str = Field(min_length=16, max_length=128)


class PublicRevealSecretRequest(PublicSecretTokenRequest):
    passphrase: str | None = Field(default=None, max_length=256)


class PublicSecretPeekResponse(APIBaseModel):
    requires_passphrase: bool
    # Withheld while a passphrase is set.
    label: str | None
    sender_name: str | None
    expires_at: datetime | None
    views_left: int | None
    locked_until: datetime | None
    attempts_left: int | None


class SecretLinkItem(APIBaseModel):
    recipient_id: int
    kind: SecretRecipientKind
    email: str | None
    url: str | None
    qr_svg: str | None


class SecretLinksResponse(APIBaseModel):
    items: list[SecretLinkItem]


class SecretLinkResponse(APIBaseModel):
    recipient_id: int
    url: str
    qr_svg: str


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class SecretAllowedUser(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: str


class SecretAllowedGroup(APIBaseModel):
    id: int
    name: str


class SecretPolicySide(APIBaseModel):
    mode: SecretPolicyMode
    allowed_user_ids: list[int]
    allowed_group_ids: list[int]
    allowed_users: list[SecretAllowedUser]
    allowed_groups: list[SecretAllowedGroup]


class SecretPolicyResponse(APIBaseModel):
    enabled: bool
    send: SecretPolicySide
    external: SecretPolicySide
    passphrase_failure_mode: PassphraseFailureMode


class UpdateSecretPolicySide(APIBaseModel):
    mode: SecretPolicyMode
    allowed_user_ids: list[int] = Field(default_factory=list, max_length=1000)
    allowed_group_ids: list[int] = Field(default_factory=list, max_length=1000)


class UpdateSecretPolicyRequest(APIBaseModel):
    # Every field optional: None = leave unchanged.
    enabled: bool | None = None
    send: UpdateSecretPolicySide | None = None
    external: UpdateSecretPolicySide | None = None
    passphrase_failure_mode: PassphraseFailureMode | None = None


class AdminSecretListItem(SecretListItem):
    sender_email: str


class AdminSecretListResponse(APIBaseModel):
    items: list[AdminSecretListItem]
    total: int = 0
    page: int = 1
    page_size: int = 50
