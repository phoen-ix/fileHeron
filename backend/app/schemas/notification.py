"""Notification + preference schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ..models.notification import NotificationCategory
from ..models.user_notification_preference import NotificationChannel
from .common import APIBaseModel


class PreferenceItem(APIBaseModel):
    category: NotificationCategory
    channel: NotificationChannel


class PreferencesResponse(APIBaseModel):
    items: list[PreferenceItem]


class UpdatePreferencesRequest(APIBaseModel):
    """Body shape: `{<category>: <channel>}`. We don't enumerate keys
    here — Pydantic's dict-keyed model would be awkward — so the router
    validates each pair after parse."""
    preferences: dict[str, str] = Field(default_factory=dict)


class NotificationItem(APIBaseModel):
    id: int
    category: NotificationCategory
    payload: dict[str, Any]
    link_url: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationListResponse(APIBaseModel):
    items: list[NotificationItem]
    unread_count: int
    page: int
    page_size: int
    total: int


class MarkReadResponse(APIBaseModel):
    ok: bool
    unread_count: int
