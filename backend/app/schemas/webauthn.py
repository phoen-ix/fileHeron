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
    # The browser's report that the passkey lives on the device - one that can
    # sign in without an email or a password. None: not reported (every passkey
    # registered before v2.27.0).
    discoverable: bool | None = None


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


class PendingPasskeyBeginRequest(APIBaseModel):
    """Finish a pending sign-in (after SSO) with a passkey. The pending-2FA token
    proves the first factor only, exactly as for /api/auth/2fa/complete."""

    pending_token: str = Field(..., min_length=1, max_length=4096)


class PendingPasskeyCompleteRequest(APIBaseModel):
    pending_token: str = Field(..., min_length=1, max_length=4096)
    session: str = Field(..., min_length=1, max_length=64)
    credential: dict[str, Any]
