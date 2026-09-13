"""Admin error-alert + error-log settings schemas.

The worker/cron source has a global default here (``source_worker``) AND a
per-task ``alert_on_failure`` override on the Scheduled tasks page (see
schemas/cron_settings.py); the per-task flag wins wherever an admin has set it.
The global one exists because the per-task flag defaults off, so an instance
where nobody walked the ~20 tasks alerted on no worker failure at all while this
page said alerting was on. This page owns the
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
    source_worker: bool
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
    # Optional on the REQUEST only, and None means "leave unchanged". Every other
    # field here is required because it has been on the wire since this model
    # existed; a newly-required field 422s any client one release behind, which
    # is the same reasoning `APIBaseModel` keeps `extra="ignore"` for. A stale
    # browser tab saving this form must not fail, and must not silently reset a
    # setting it has never heard of either.
    source_worker: bool | None = None
    recipients_mode: Literal["admins", "custom"]
    custom_recipients: list[EmailLike] = Field(default_factory=list, max_length=50)
    cooldown_minutes: int = Field(ge=1, le=1440)
    max_per_hour: int = Field(ge=1, le=1000)
    log_enabled: bool
    capture_4xx: bool
    http_4xx_codes: list[Http4xxCode] = Field(default_factory=list, max_length=50)
    retention_days: int = Field(ge=0, le=3650)
