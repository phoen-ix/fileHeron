"""Admin SSO-providers schemas (Phase 10)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ..models.oidc_provider import OIDCPreset
from .common import APIBaseModel


class OIDCProviderItem(APIBaseModel):
    """Returned by GET /api/admin/settings/sso/providers (list) and
    GET .../providers/{id} (single). Secret is never exposed -
    `client_secret_set` says whether one is stored."""

    id: str
    name: str
    preset: OIDCPreset
    issuer_url: str
    client_id: str
    client_secret_set: bool
    redirect_uri: str
    enabled: bool
    user_count: int
    created_at: datetime
    updated_at: datetime


class OIDCProviderListResponse(APIBaseModel):
    items: list[OIDCProviderItem]


class CreateOIDCProviderRequest(APIBaseModel):
    name: str = Field(min_length=1, max_length=120)
    preset: OIDCPreset
    issuer_url: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=200)
    # Required on create.
    client_secret: str = Field(min_length=1, max_length=4096)
    redirect_uri: str = Field(default="", max_length=500)
    enabled: bool = True


class UpdateOIDCProviderRequest(APIBaseModel):
    name: str | None = Field(default=None, max_length=120)
    preset: OIDCPreset | None = None
    issuer_url: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=200)
    # null = leave alone; "" = clear (disables provider until set again);
    # any other string = replace.
    client_secret: str | None = None
    redirect_uri: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class TestConnectionRequest(APIBaseModel):
    issuer_url: str | None = Field(default=None, max_length=500)


class TestConnectionResponse(APIBaseModel):
    ok: bool
    issuer: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    error: str | None = None


class PresetField(APIBaseModel):
    key: str
    label: str
    placeholder: str = ""


class PresetMeta(APIBaseModel):
    preset: OIDCPreset
    label: str
    issuer: str | None = None
    issuer_template: str | None = None
    issuer_template_fields: list[PresetField] = Field(default_factory=list)
    notes: str = ""


class PresetsResponse(APIBaseModel):
    presets: list[PresetMeta]


# ---------------------------------------------------------------------------
# Public config: what /api/config-public exposes about enabled providers
# so the SPA's login page can render one button per provider.
# ---------------------------------------------------------------------------


class PublicProviderItem(APIBaseModel):
    id: str
    name: str
    preset: OIDCPreset


class PublicProvider(APIBaseModel):
    id: str
    name: str
    preset: str


class PublicBranding(APIBaseModel):
    logo_url: str | None = None
    link_url: str | None = None
    show_header: bool
    show_login: bool
    show_public: bool


class PublicLegal(APIBaseModel):
    imprint_enabled: bool
    privacy_enabled: bool


class PublicMotd(APIBaseModel):
    text: str


class PublicMaintenance(APIBaseModel):
    enabled: bool
    message: str


class PublicConfigResponse(APIBaseModel):
    """The anonymous login surface's whole contract. `motd`/`maintenance` were
    OMITTED when off; under a response_model they serialise as null instead,
    which every consumer already treats the same way (both are falsy, and the
    SPA type declares them optional)."""
    app_name: str
    default_locale: str
    providers: list[PublicProvider]
    site_timezone: str
    max_direct_upload_bytes: int
    branding: PublicBranding
    legal: PublicLegal
    motd: PublicMotd | None = None
    maintenance: PublicMaintenance | None = None
