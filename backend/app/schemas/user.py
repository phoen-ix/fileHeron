"""User-facing schemas."""
from __future__ import annotations

from .common import APIBaseModel


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
