"""Quarantine admin actions + settings."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QuarantineActionRequest(BaseModel):
    """Body for release / purge — admin must justify the action so the
    audit row carries forensic context."""
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=500)


class QuarantineSettingsResponse(BaseModel):
    notify_admins: bool


class UpdateQuarantineSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify_admins: bool
