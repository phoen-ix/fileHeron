"""Auth-flow request / response schemas."""
from __future__ import annotations

from pydantic import Field

from ..models.user import Locale
from .common import APIBaseModel
from .types import EmailLike


class RegisterFromInviteRequest(APIBaseModel):
    token: str = Field(..., min_length=10, max_length=128)
    password: str = Field(..., min_length=12, max_length=256)
    display_name: str = Field(..., min_length=1, max_length=120)
    locale: Locale = Locale.en


class LoginRequest(APIBaseModel):
    email: EmailLike
    password: str = Field(..., min_length=1, max_length=256)
    # Optional: only required when the user has 2FA enabled.
    # On first attempt without a code, server returns 401 TOTP_REQUIRED so the
    # client can prompt and retry with the same email/password + a TOTP code.
    totp_code: str | None = Field(default=None, min_length=6, max_length=8)


class LoginRecoveryRequest(APIBaseModel):
    email: EmailLike
    password: str = Field(..., min_length=1, max_length=256)
    recovery_code: str = Field(..., min_length=4, max_length=32)


class CompleteSecondFactorRequest(APIBaseModel):
    """Exchange a pending-2FA token for a real session.

    `pending_token` proves the FIRST factor only (OIDC or a passkey) and grants
    nothing on its own - this is the only endpoint that accepts it. Exactly one
    of totp_code / recovery_code is expected; recovery is accepted because
    otherwise an SSO user who loses their authenticator has no way back in.
    """

    pending_token: str = Field(..., min_length=1, max_length=4096)
    totp_code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=1, max_length=64)


class LoginResponse(APIBaseModel):
    access_token: str
    expires_in_seconds: int


class RefreshResponse(APIBaseModel):
    access_token: str
    expires_in_seconds: int


class ForgotPasswordRequest(APIBaseModel):
    email: EmailLike


class ResetPasswordRequest(APIBaseModel):
    token: str = Field(..., min_length=10, max_length=128)
    new_password: str = Field(..., min_length=12, max_length=256)


class VerifyEmailRequest(APIBaseModel):
    token: str = Field(..., min_length=10, max_length=128)


class ConfirmEmailChangeRequest(APIBaseModel):
    """Public confirm of a pending email change - the token IS the auth."""
    token: str = Field(..., min_length=10, max_length=128)


class CancelEmailChangeRequest(APIBaseModel):
    """Old-address 'it wasn't me' kill switch for a pending email change."""
    token: str = Field(..., min_length=10, max_length=128)


class ResendVerificationResponse(APIBaseModel):
    """`already_verified` is only present on the short-circuit path; the model
    makes it explicit rather than leaving the two shapes undeclared."""
    ok: bool = True
    already_verified: bool = False


class ConfirmEmailChangeResponse(APIBaseModel):
    ok: bool = True
    applied: bool
    pending_side: str | None = None
    oidc_reset: bool = False
    set_password_required: bool = False


class WebAuthnAuthCompleteResponse(APIBaseModel):
    """Two shapes: a real session, or the half-authenticated pending-2FA token
    when the account has TOTP enrolled (v2.12.0)."""
    access_token: str | None = None
    expires_in_seconds: int | None = None
    pending_2fa_token: str | None = None
