"""Site admin settings - URL + display timezone. See ``services/site.py``."""
from __future__ import annotations

from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator


class SiteSettingsResponse(BaseModel):
    site_url: str          # current effective value (kv → env fallback)
    has_db_override: bool  # whether the value comes from kv (url)
    env_app_url: str       # the env value (so the UI can show "fallback to:")
    site_timezone: str     # IANA name, e.g. "Europe/Vienna" or "UTC"


class UpdateSiteSettingsRequest(BaseModel):
    """Field-level PATCH semantics:

    - ``site_url``: ``None`` leaves URL unchanged; any provided value
      becomes the new override (empty string clears back to env fallback).
    - ``site_timezone``: ``None`` leaves tz unchanged; ``""`` clears back
      to default (UTC); any other value must be a valid IANA zone name.

    The router is responsible for distinguishing "field not in payload"
    (leave alone) from "field present" (write). Pydantic v2 with
    ``extra="forbid"`` rejects unknown fields.
    """
    model_config = ConfigDict(extra="forbid")

    site_url: str | None = None
    site_timezone: str | None = None

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

    @field_validator("site_timezone")
    @classmethod
    def _validate_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return ""  # explicit clear sentinel - router resets to default
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(
                f"Unknown timezone {v!r} - must be an IANA name like 'Europe/Vienna' or 'UTC'."
            ) from e
        return v
