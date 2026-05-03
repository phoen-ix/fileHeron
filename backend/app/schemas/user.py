"""User-facing schemas."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel
from .types import EmailLike


class UserLookupRequest(APIBaseModel):
    email: EmailLike


class UserLookupResponse(APIBaseModel):
    user_id: int = Field(..., gt=0)
    display_name: str
    email: str


class UserSearchItem(APIBaseModel):
    user_id: int
    display_name: str
    email: str
    role: str


class UserSearchResponse(APIBaseModel):
    items: list[UserSearchItem]


class ConnectionItem(APIBaseModel):
    user_id: int
    display_name: str
    email: str
    role: str
    sources: list[str]  # e.g. ["invite"], ["shared_group"], ["invite", "shared_group"]


class ConnectionListResponse(APIBaseModel):
    items: list[ConnectionItem]
