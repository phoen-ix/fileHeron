"""Site URL admin settings — see ``services/site.py``."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator


class SiteSettingsResponse(BaseModel):
    site_url: str          # current effective value (kv → env fallback)
    has_db_override: bool  # whether the value comes from kv
    env_app_url: str       # the env value (so the UI can show "fallback to:")


class UpdateSiteSettingsRequest(BaseModel):
    """``site_url=None`` clears the kv override and reverts to the env
    fallback. Any other value must be a parseable HTTP(S) URL."""
    model_config = ConfigDict(extra="forbid")

    site_url: str | None = None

    @field_validator("site_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None  # treat empty string as "clear" too
        if not v.startswith(("http://", "https://")):
            raise ValueError("Site URL must start with http:// or https://")
        parsed = urlparse(v)
        if not parsed.netloc:
            raise ValueError("Site URL must include a host (e.g. https://files.example.com)")
        return v.rstrip("/")
