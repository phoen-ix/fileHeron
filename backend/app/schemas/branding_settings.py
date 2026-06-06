"""Schemas for admin site-branding settings (logo surfaces + link)."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import ConfigDict, field_validator

from .common import APIBaseModel


class BrandingLogoMeta(APIBaseModel):
    present: bool
    filename: str | None = None
    content_type: str | None = None
    # Stable public URL (None when no logo is stored).
    url: str | None = None


class BrandingSettingsResponse(APIBaseModel):
    logo: BrandingLogoMeta
    show_header: bool
    show_login: bool
    show_public: bool
    show_email: bool
    show_client: bool
    link_url: str | None = None


class UpdateBrandingSettingsRequest(APIBaseModel):
    """PATCH semantics: only fields present in the body are written. A null
    ``link_url`` (or empty string) clears the link."""
    model_config = ConfigDict(extra="forbid")

    show_header: bool | None = None
    show_login: bool | None = None
    show_public: bool | None = None
    show_email: bool | None = None
    show_client: bool | None = None
    link_url: str | None = None

    @field_validator("link_url")
    @classmethod
    def _validate_link(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return ""  # explicit clear sentinel
        if not v.startswith(("http://", "https://")):
            raise ValueError("Link must start with http:// or https://")
        if not urlparse(v).netloc:
            raise ValueError("Link must include a host (e.g. https://example.com)")
        return v
