"""Schemas for the /setup wizard."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel


class SetupStatusResponse(APIBaseModel):
    # True when no admin exists yet - SPA shows the wizard.
    required: bool
    # True when the wizard will demand SETUP_TOKEN; the SPA shows the field
    # when the URL did not carry it.
    token_required: bool = False


class CompleteSetupRequest(APIBaseModel):
    # Loose validation here (basic format); the service normalises and
    # rejects duplicates. Matches the rest of the codebase, which doesn't
    # use Pydantic's strict EmailStr (rejects .local TLDs etc).
    email: str = Field(..., min_length=3, max_length=254, pattern=r".+@.+\..+")
    password: str = Field(..., min_length=12, max_length=512)
    display_name: str = Field(..., min_length=1, max_length=120)
    setup_token: str | None = Field(default=None, max_length=256)
    # The language the wizard was shown in. The first admin was always created
    # `en`, so a German operator got English mail until they found the
    # setting; every later account then inherited that from its creator
    # (audit 2026-11-08). Optional: an older SPA sends nothing.
    locale: Literal["en", "de"] | None = None


class CompleteSetupResponse(APIBaseModel):
    user_id: int
    email: str
