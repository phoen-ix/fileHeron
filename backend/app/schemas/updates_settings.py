"""Schemas for the admin-configurable update-check (Phase 5 self-update).

`api_url` is the full URL of the GitHub-compatible releases API endpoint the
release-check cron polls. Fork operators point this at their own repo. Whether/
how often the check runs is set on the Scheduled tasks page (cron 'release_check')
as of v1.28.0.
"""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class UpdatesSettingsResponse(APIBaseModel):
    api_url: str


class UpdateUpdatesSettingsRequest(APIBaseModel):
    api_url: str = Field(..., min_length=1, max_length=512)
