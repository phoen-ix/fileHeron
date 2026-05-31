"""Schemas for the generic registry-driven 'Advanced settings' admin page."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AdvancedSettingItem(BaseModel):
    key: str
    group: str
    kind: str               # "int" | "bool" | "str"
    value: Any              # effective value (kv override or env default)
    default: Any            # env default
    is_overridden: bool     # True if an app_settings row exists for this key
    min: int | None = None
    max: int | None = None


class AdvancedSettingsResponse(BaseModel):
    items: list[AdvancedSettingItem]


class UpdateAdvancedSettingsRequest(BaseModel):
    # {key: value} to set, or {key: null} to reset that key to its env default.
    updates: dict[str, Any]
