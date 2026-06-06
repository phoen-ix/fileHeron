"""Admin share-approval policy schemas (v1.24.0)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel

ApproverMode = Literal["admins_only", "employees_admins"]
ApprovalScope = Literal["outbound", "all", "outbound_to_clients"]


class ApproverUserRef(APIBaseModel):
    id: int
    display_name: str
    email: str
    role: str


class ApproverGroupRef(APIBaseModel):
    id: int
    name: str


class ShareApprovalSettingsResponse(APIBaseModel):
    enabled: bool
    approver_mode: ApproverMode
    approver_user_ids: list[int]
    approver_group_ids: list[int]
    approver_users: list[ApproverUserRef]
    approver_groups: list[ApproverGroupRef]
    scope: ApprovalScope
    exempt_approvers: bool
    allow_content_review: bool


class UpdateShareApprovalSettingsRequest(APIBaseModel):
    enabled: bool
    approver_mode: ApproverMode
    approver_user_ids: list[int] = Field(default_factory=list)
    approver_group_ids: list[int] = Field(default_factory=list)
    scope: ApprovalScope
    exempt_approvers: bool
    allow_content_review: bool
