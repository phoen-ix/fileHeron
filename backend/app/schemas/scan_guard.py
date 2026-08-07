"""Admin schemas for the scan guard (/api/admin/scan-guard)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import APIBaseModel


class ScanGuardSettingsResponse(APIBaseModel):
    enabled: bool
    signal_probe_path: bool
    signal_api_404: bool
    signal_auth_failure: bool
    escalation: bool
    network_escalation: bool
    notify_mode: Literal["off", "digest", "every_block"]
    allowlist: str
    extra_paths: str
    ignore_paths: str
    # Bounds mirror services/settings_registry.py so the form refuses what the
    # registry would clamp, instead of silently storing a different number.
    threshold: int = Field(ge=1, le=1000)
    window_sec: int = Field(ge=30, le=86400)
    block_minutes: int = Field(ge=1, le=43200)
    max_block_minutes: int = Field(ge=1, le=43200)
    min_distinct_paths: int = Field(ge=1, le=500)
    network_threshold: int = Field(ge=2, le=254)
    network_lookback_hours: int = Field(ge=1, le=8760)
    max_new_blocks_per_min: int = Field(ge=1, le=10000)
    # Live counts, so the page can say what is in force without a second call.
    active_ip_blocks: int = 0
    active_network_blocks: int = 0


class UpdateScanGuardSettingsRequest(APIBaseModel):
    enabled: bool
    signal_probe_path: bool
    signal_api_404: bool
    signal_auth_failure: bool
    escalation: bool
    network_escalation: bool
    notify_mode: Literal["off", "digest", "every_block"] = "digest"
    allowlist: str = Field(default="", max_length=4000)
    extra_paths: str = Field(default="", max_length=4000)
    ignore_paths: str = Field(default="", max_length=4000)
    threshold: int = Field(ge=1, le=1000)
    window_sec: int = Field(ge=30, le=86400)
    block_minutes: int = Field(ge=1, le=43200)
    max_block_minutes: int = Field(ge=1, le=43200)
    min_distinct_paths: int = Field(ge=1, le=500)
    network_threshold: int = Field(ge=2, le=254)
    network_lookback_hours: int = Field(ge=1, le=8760)
    max_new_blocks_per_min: int = Field(ge=1, le=10000)


class IpBlockRow(APIBaseModel):
    id: int
    subject: str
    network: str
    is_network: bool
    reason: str
    source: str
    hit_count: int
    strikes: int
    last_path: str | None
    created_at: datetime
    expires_at: datetime
    released_at: datetime | None
    note: str | None


class IpBlockListResponse(APIBaseModel):
    items: list[IpBlockRow]
    total: int
    page: int
    page_size: int


class CreateIpBlockRequest(APIBaseModel):
    """Manual block. `minutes` is bounded by the same ceiling the automatic
    ladder is: there is deliberately no permanent block anywhere in this
    feature, so every mistake self-heals unattended."""
    subject: str = Field(min_length=3, max_length=64)
    minutes: int = Field(default=60, ge=1, le=43200)
    note: str | None = Field(default=None, max_length=255)
