"""Admin email/SMTP settings schemas (post-Phase 10)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIBaseModel

TlsMode = Literal["implicit", "starttls", "none"]


class EmailSettingsResponse(APIBaseModel):
    """Returned by GET /api/admin/settings/email.

    The password is never echoed. `is_password_set` lets the UI render
    the right placeholder ("(set — leave blank to keep)" vs
    "Paste the SMTP password")."""
    host: str
    port: int
    user: str
    is_password_set: bool
    from_email: str
    from_name: str
    tls_mode: TlsMode
    # True when at least the host is non-empty — i.e. the live config
    # would attempt a real send. False = logs-fallback in dev.
    is_configured: bool
    # True when the *effective* value comes from a DB row (vs the env
    # fallback). Surfaces the "managed by .env" hint in the UI for
    # operators who haven't migrated yet.
    has_db_overrides: bool


class UpdateEmailSettingsRequest(APIBaseModel):
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = Field(default=None, max_length=255)
    # null  = leave existing alone (admin doesn't have to re-enter)
    # ""    = clear (no auth)
    # other = replace
    password: str | None = None
    from_email: str | None = Field(default=None, max_length=255)
    from_name: str | None = Field(default=None, max_length=120)
    tls_mode: TlsMode | None = None


class TestEmailRequest(APIBaseModel):
    to: str = Field(..., min_length=3, max_length=320)
    # When set, the test uses these values (typically the unsaved form
    # state) instead of the persisted config. Only the fields the admin
    # edits in the UI; password follows the same null-keep semantics
    # as the PUT endpoint.
    override: UpdateEmailSettingsRequest | None = None


class TestEmailResponse(APIBaseModel):
    ok: bool
    error_class: str | None = None
    error_message: str | None = None
    smtp_code: int | None = None
