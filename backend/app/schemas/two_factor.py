"""2FA setup / enable / disable / recovery + session listing schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import APIBaseModel


class TotpSetupResponse(APIBaseModel):
    """Returned by POST /api/account/2fa/setup. The plaintext secret + URI are
    shown once so the user's authenticator app can pair. Caller MUST follow up
    with /api/account/2fa/enable to activate 2FA."""
    secret_b32: str
    otpauth_uri: str
    qr_svg: str


class TotpEnableRequest(APIBaseModel):
    code: str = Field(..., min_length=6, max_length=8)


class RecoveryCodesResponse(APIBaseModel):
    """One-time response containing plaintext recovery codes. The server
    only stores Argon2 hashes; if the user loses these, /regenerate is the
    way to get a new set (which invalidates the old set)."""
    recovery_codes: list[str]


class TotpDisableRequest(APIBaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    code_or_recovery: str = Field(..., min_length=4, max_length=32)


class RecoveryCodeRegenerateRequest(APIBaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    code_or_recovery: str = Field(..., min_length=4, max_length=32)


class TotpStatusResponse(APIBaseModel):
    enabled: bool
    enabled_at: datetime | None
    recovery_codes_remaining: int


class SessionResponse(APIBaseModel):
    id: int
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime
    created_ip: str | None = None
    created_ua: str | None = None
    is_current: bool


class SessionListResponse(APIBaseModel):
    items: list[SessionResponse]
