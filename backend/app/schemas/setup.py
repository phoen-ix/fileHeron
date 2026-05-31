"""Schemas for the /setup wizard."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class SetupStatusResponse(APIBaseModel):
    # True when no admin exists yet — SPA shows the wizard.
    required: bool


class CompleteSetupRequest(APIBaseModel):
    # Loose validation here (basic format); the service normalises and
    # rejects duplicates. Matches the rest of the codebase, which doesn't
    # use Pydantic's strict EmailStr (rejects .local TLDs etc).
    email: str = Field(..., min_length=3, max_length=254, pattern=r".+@.+\..+")
    password: str = Field(..., min_length=12, max_length=512)
    display_name: str = Field(..., min_length=1, max_length=120)


class CompleteSetupResponse(APIBaseModel):
    user_id: int
    email: str
