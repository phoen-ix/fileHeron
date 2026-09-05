"""WebAuthn / passkey schemas (Phase 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import APIBaseModel


class WebAuthnCredentialItem(APIBaseModel):
    id: int
    name: str
    transports: list[str]
    created_at: datetime
    last_used_at: datetime | None


class WebAuthnCredentialListResponse(APIBaseModel):
    items: list[WebAuthnCredentialItem]


class WebAuthnRegisterCompleteRequest(APIBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    credential: dict[str, Any]


class WebAuthnRegisterBeginRequest(APIBaseModel):
    """Re-auth for adding a passkey. A UV-verified passkey satisfies the
    account's second factor at login, so registering one must cost the same
    as disabling TOTP does: the current password, not just a live session."""

    password: str = Field(..., min_length=1, max_length=255)


class WebAuthnRegisterBeginResponse(APIBaseModel):
    options: dict[str, Any]


class WebAuthnAuthBeginRequest(APIBaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=255)


class WebAuthnAuthBeginResponse(APIBaseModel):
    session: str
    options: dict[str, Any]


class WebAuthnAuthCompleteRequest(APIBaseModel):
    session: str
    credential: dict[str, Any]
