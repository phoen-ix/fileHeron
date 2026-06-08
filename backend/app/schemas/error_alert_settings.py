"""Admin error-alert settings schemas (email admins on server errors).

The cron/worker source isn't here - it's a per-task ``alert_on_failure`` toggle
on the Scheduled tasks page (see schemas/cron_settings.py). This page owns the
master switch, the HTTP-5xx source, recipient targeting, and the saferail knobs.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel
from .types import EmailLike


class ErrorAlertSettingsResponse(APIBaseModel):
    enabled: bool
    source_http_5xx: bool
    recipients_mode: Literal["admins", "custom"]
    custom_recipients: list[EmailLike]
    cooldown_minutes: int
    max_per_hour: int


class UpdateErrorAlertSettingsRequest(APIBaseModel):
    enabled: bool
    source_http_5xx: bool
    recipients_mode: Literal["admins", "custom"]
    custom_recipients: list[EmailLike] = Field(default_factory=list, max_length=50)
    cooldown_minutes: int = Field(ge=1, le=1440)
    max_per_hour: int = Field(ge=1, le=1000)
