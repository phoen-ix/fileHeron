"""Admin error-alert + error-log settings schemas.

The cron/worker source isn't here - it's a per-task ``alert_on_failure`` toggle
on the Scheduled tasks page (see schemas/cron_settings.py). This page owns the
master alert switch, the HTTP-5xx/4xx sources, recipient targeting, the saferail
knobs, and the (decoupled) error-LOG switches: ``log_enabled``, ``capture_4xx``,
the 4xx allowlist, and the log-retention window. Logging persists every
qualifying error to the browsable Error log; alerting is the throttled subset.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel
from .types import EmailLike

# A 4xx HTTP status code (the allowlist that governs both 4xx capture + alert).
Http4xxCode = int


class ErrorAlertSettingsResponse(APIBaseModel):
    enabled: bool
    source_http_5xx: bool
    source_http_4xx: bool
    recipients_mode: Literal["admins", "custom"]
    custom_recipients: list[EmailLike]
    cooldown_minutes: int
    max_per_hour: int
    # Error log (decoupled from the alert switches above).
    log_enabled: bool
    capture_4xx: bool
    http_4xx_codes: list[Http4xxCode]
    retention_days: int


class UpdateErrorAlertSettingsRequest(APIBaseModel):
    enabled: bool
    source_http_5xx: bool
    source_http_4xx: bool
    recipients_mode: Literal["admins", "custom"]
    custom_recipients: list[EmailLike] = Field(default_factory=list, max_length=50)
    cooldown_minutes: int = Field(ge=1, le=1440)
    max_per_hour: int = Field(ge=1, le=1000)
    log_enabled: bool
    capture_4xx: bool
    http_4xx_codes: list[Http4xxCode] = Field(default_factory=list, max_length=50)
    retention_days: int = Field(ge=0, le=3650)
