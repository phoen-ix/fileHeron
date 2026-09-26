"""Schemas for the admin-configurable update-check (Phase 5 self-update).

`api_url` is the full URL of the GitHub-compatible releases API endpoint the
release-check cron polls. Fork operators point this at their own repo. Whether/
how often the check runs is set on the Scheduled tasks page (cron 'release_check')
as of v1.28.0.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel

AutoUpdateScope = Literal["patch", "minor", "any"]


class UpdatesSettingsResponse(APIBaseModel):
    api_url: str


class UpdateUpdatesSettingsRequest(APIBaseModel):
    api_url: str = Field(..., min_length=1, max_length=512)


class AutoUpdateSettingsResponse(APIBaseModel):
    enabled: bool
    scope: AutoUpdateScope
    min_age_hours: int
    # The release whose automatic install failed; not retried automatically.
    skipped_tag: str | None = None


class UpdateAutoUpdateSettingsRequest(APIBaseModel):
    """Every field optional: None leaves it unchanged. `password` is required
    whenever the result is ON and something changed - an automatic update
    skips the password a manual one asks for."""
    enabled: bool | None = None
    scope: AutoUpdateScope | None = None
    min_age_hours: int | None = Field(default=None, ge=0, le=720)
    password: str | None = Field(default=None, max_length=512)
