"""Group schemas (Phase 4)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import APIBaseModel


class CreateGroupRequest(APIBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    is_company_inbox: bool = False


class UpdateGroupRequest(APIBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    is_company_inbox: bool | None = None


class GroupMemberItem(APIBaseModel):
    user_id: int
    display_name: str
    email: str
    role: str
    joined_at: datetime


class GroupResponse(APIBaseModel):
    id: int
    name: str
    description: str | None
    is_company_inbox: bool
    created_at: datetime
    created_by_id: int
    member_count: int


class GroupDetailResponse(GroupResponse):
    members: list[GroupMemberItem]


class GroupListResponse(APIBaseModel):
    items: list[GroupResponse]


class AddGroupMembersRequest(APIBaseModel):
    user_ids: list[int] = Field(..., min_length=1)
