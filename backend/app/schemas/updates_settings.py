"""Schemas for the admin-configurable update-check (Phase 5 self-update).

`api_url` is the full URL of the GitHub-compatible releases API endpoint
the release-check cron polls. Fork operators point this at their own
repo. `check_mode` controls whether the cron does anything when it
fires hourly: `auto` does a real check at most once per 24h, `manual`
skips entirely (admins click Check now)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel


class UpdatesSettingsResponse(APIBaseModel):
    api_url: str
    check_mode: Literal["auto", "manual"]


class UpdateUpdatesSettingsRequest(APIBaseModel):
    api_url: str = Field(..., min_length=1, max_length=512)
    check_mode: Literal["auto", "manual"]
