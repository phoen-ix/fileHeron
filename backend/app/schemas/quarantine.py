"""Quarantine admin actions + settings."""
from __future__ import annotations

from datetime import datetime

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


class AvStatusResponse(BaseModel):
    """Read-only snapshot of the running ClamAV engine. Always
    renderable — `available=False` rows still carry context (av_skip
    for dev, error for "unreachable")."""
    available: bool
    av_skip: bool
    version: str | None = None        # e.g. "ClamAV 1.5.2"
    sigs_version: str | None = None   # e.g. "27543"
    sigs_date: str | None = None      # ctime-style string from clamd
    raw: str | None = None            # full VERSION reply, for debugging
    error: str | None = None          # populated when available=False
    last_reload_at: datetime | None = None


class AvReloadResponse(BaseModel):
    ok: bool
    av_skip: bool
    raw: str
