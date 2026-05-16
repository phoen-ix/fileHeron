"""Schemas for the admin-set login-page MOTD banner."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class MotdSettingsResponse(APIBaseModel):
    enabled: bool
    text: str = ""


class UpdateMotdSettingsRequest(APIBaseModel):
    enabled: bool
    # Plain text; max ~500 chars to keep the login page from getting
    # taken over by a long banner. Empty string is allowed (= clear).
    text: str = Field(default="", max_length=500)
