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
    watchlist: bool = True
    notify_mode: Literal["off", "every_block"]
    # Read-only here. The allowlist is written through the dedicated endpoints
    # (see UpdateScanGuardSettingsRequest), but it stays in the GET so the
    # settings page can still show what is exempt.
    allowlist: str
    extra_paths: str
    ignore_paths: str
    # Bounds mirror services/settings_registry.py so the form refuses what the
    # registry would clamp, instead of silently storing a different number.
    threshold: int = Field(ge=1, le=1000)
    auth_threshold: int = Field(default=15, ge=5, le=500)
    window_sec: int = Field(ge=30, le=86400)
    block_minutes: int = Field(ge=1, le=43200)
    max_block_minutes: int = Field(ge=1, le=43200)
    min_distinct_paths: int = Field(ge=1, le=500)
    network_threshold: int = Field(ge=2, le=254)
    network_lookback_hours: int = Field(ge=1, le=8760)
    max_new_blocks_per_min: int = Field(ge=1, le=10000)
    network_prefix_v6: int = Field(default=64, ge=56, le=128)
    # Live counts, so the page can say what is in force without a second call.
    active_ip_blocks: int = 0
    active_network_blocks: int = 0


class UpdateScanGuardSettingsRequest(APIBaseModel):
    """The POLICY half of the scan guard.

    `allowlist` is deliberately NOT a field. It is state, owned by
    `POST/DELETE /api/admin/scan-guard/allowlist`, which serialise on a row lock.
    Accepting it here too made the settings form a second writer carrying a
    whole-CSV snapshot, so an admin who had the page open while allowlisting an
    address elsewhere erased it on save. `APIBaseModel` does not forbid extra
    fields, so an older SPA that still sends it is ignored rather than 422'd.
    """

    enabled: bool
    signal_probe_path: bool
    signal_api_404: bool
    signal_auth_failure: bool
    escalation: bool
    network_escalation: bool
    watchlist: bool = True
    notify_mode: Literal["off", "every_block"] = "off"
    extra_paths: str = Field(default="", max_length=4000)
    ignore_paths: str = Field(default="", max_length=4000)
    threshold: int = Field(ge=1, le=1000)
    # Defaulted for the same reason network_prefix_v6 is, below.
    auth_threshold: int = Field(default=15, ge=5, le=500)
    window_sec: int = Field(ge=30, le=86400)
    block_minutes: int = Field(ge=1, le=43200)
    max_block_minutes: int = Field(ge=1, le=43200)
    min_distinct_paths: int = Field(ge=1, le=500)
    network_threshold: int = Field(ge=2, le=254)
    network_lookback_hours: int = Field(ge=1, le=8760)
    max_new_blocks_per_min: int = Field(ge=1, le=10000)
    # Defaulted, unlike its neighbours: adding a REQUIRED int here would 422
    # every existing client's PUT, including the shipped SPA, until both moved
    # in the same commit.
    network_prefix_v6: int = Field(default=64, ge=56, le=128)


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
    released_by_id: int | None = None
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


class AllowlistResponse(APIBaseModel):
    """`invalid` holds stored entries the guard cannot parse and therefore never
    enforces. They are reported rather than hidden: an admin must not believe an
    entry protects an address when it does nothing."""
    entries: list[str]
    invalid: list[str]


class AllowlistAddRequest(APIBaseModel):
    entry: str = Field(min_length=3, max_length=64)


class AllowBlockResponse(APIBaseModel):
    block: IpBlockRow
    allowlist: list[str]


class ReleaseAllResponse(APIBaseModel):
    released: int


class WatchRow(APIBaseModel):
    ip: str
    offences: int
    last_signal: str | None
    last_path: str | None
    last_seen: datetime | None


class WatchlistResponse(APIBaseModel):
    """`available` is False when Redis could not answer. The page renders a
    notice and keeps working: the DB-backed block table is the load-bearing
    half, so a watchlist outage must never fail the whole request."""
    available: bool
    enabled: bool
    window_sec: int
    threshold: int
    auth_threshold: int
    items: list[WatchRow]
