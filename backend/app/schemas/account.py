"""Account self-service schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ..models.user import Locale, UserRole
from .common import APIBaseModel
from .types import EmailLike


class MeResponse(APIBaseModel):
    id: int
    email: str
    display_name: str
    role: UserRole
    locale: Locale
    email_verified: bool
    is_disabled: bool
    created_at: datetime
    last_login_at: datetime | None
    quota_bytes: int | None
    # Derived from the public-link policy. SPA hides the inline-create
    # toggle in /share/new when False. Re-evaluated on every /me fetch
    # so policy changes propagate on the next refresh.
    can_create_public_link: bool = True
    # Per-user post-login destination. NULL = use system default.
    # Validated against `services/account_prefs.ALLOWED_LANDING_ROUTES`
    # in the PATCH endpoint.
    default_landing_page: str | None = None
    # Global flag from `app_settings.home_page.enabled`. When False
    # the SPA hides the home option in the landing-page picker, makes
    # the brand mark non-clickable, and bounces `/` forward.
    home_page_enabled: bool = True
    # True when the active 2FA policy applies to this user and they
    # haven't enabled TOTP yet. The SPA route guard reads this and
    # redirects to /account/2fa/forced. Computed live by
    # `services.twofa_policy.is_2fa_required` — flips false on the
    # next /me hydration after the user finishes setup.
    requires_2fa: bool = False
    # Default state for the per-share "Notify recipient(s)" checkbox on
    # the create-share form. Sourced from the kv
    # `share.notify_recipients_default` (admin-editable). The SPA uses
    # this to pre-check the checkbox; the sender can still override
    # per share.
    share_notify_recipients_default: bool = True
    # Whether self-service email change is enabled (kv
    # `email_change.self_service`, admin-editable). When False the SPA
    # hides the "Change email" block on the Account page. Admin-initiated
    # email change is unaffected.
    can_change_own_email: bool = False
    # Global in-browser-preview switch (kv `file_preview.enabled`, admin-set,
    # default true). When False the SPA hides every Preview button; the preview
    # endpoints also refuse server-side (403). Re-read on each /me hydration.
    file_preview_enabled: bool = True
    # Per-admin collapsible-sidebar mode. NULL = system default (accordion).
    # Validated against `services/account_prefs.ADMIN_NAV_MODES` in the PATCH
    # endpoint. Only meaningful for admins; harmless null for everyone else.
    admin_nav_collapse_mode: str | None = None
    # Set of currently-open sidebar category keys, synced across devices.
    # NULL = never set (client uses the mode's default). [] = all collapsed
    # (explicit, distinct from NULL).
    admin_nav_open_categories: list[str] | None = None


class ChangePasswordRequest(APIBaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=12, max_length=256)


class RequestEmailChangeRequest(APIBaseModel):
    """Body for POST /api/account/email — self-service email change.
    Re-authenticates with the current password before staging the change."""
    new_email: EmailLike
    current_password: str = Field(..., min_length=1, max_length=256)


class UpdateLocaleRequest(APIBaseModel):
    """Body for PATCH /api/account/locale."""
    locale: Locale


class UpdateDisplayNameRequest(APIBaseModel):
    """Body for PATCH /api/account/display-name."""
    display_name: str = Field(..., min_length=1, max_length=120)


class UpdateDefaultLandingPageRequest(APIBaseModel):
    """Body for PATCH /api/account/default-landing-page.

    `null` clears the preference (user falls back to system default).
    Otherwise must be one of `services/account_prefs.ALLOWED_LANDING_ROUTES`.
    The route handler additionally refuses `"home"` when the admin
    has disabled the home page.
    """
    default_landing_page: str | None = None


class UpdateAdminNavModeRequest(APIBaseModel):
    """Body for PATCH /api/account/admin-nav-mode.

    `null` clears the preference (system default = accordion). Otherwise must
    be one of `services/account_prefs.ADMIN_NAV_MODES`; the route handler
    validates and returns 400 INVALID_ADMIN_NAV_MODE on a bad value (rather
    than a Pydantic 422, mirroring how `default-landing-page` validates).
    """
    mode: str | None = None


class UpdateAdminNavOpenRequest(APIBaseModel):
    """Body for PATCH /api/account/admin-nav-open.

    `open` is the set of open category keys. `[]` is a valid explicit value
    (all collapsed). Each key must be in
    `services/account_prefs.ADMIN_NAV_CATEGORIES`; the route returns 400
    INVALID_ADMIN_NAV_CATEGORY otherwise.
    """
    open: list[str] = Field(default_factory=list)


class InviteRequest(APIBaseModel):
    """Body for POST /api/account/invite (employees+admins only).

    `target_role` defaults to client. `initial_group_ids` (optional) lets the
    inviter pre-assign group memberships that get applied when the invite is
    consumed.
    """
    email: EmailLike
    display_name_hint: str = Field(..., min_length=1, max_length=120)
    target_role: UserRole = UserRole.client
    initial_group_ids: list[int] = Field(default_factory=list)
