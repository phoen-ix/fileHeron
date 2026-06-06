"""Admin scheduled-tasks (cron) schemas (v1.28.0)."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class CronCounts(APIBaseModel):
    success: int
    failure: int
    running: int


class CronScheduleItem(APIBaseModel):
    name: str
    group: str
    description: str
    enabled: bool
    kind: str  # interval | daily
    interval_minutes: int
    daily_time: str
    min_interval_minutes: int
    # Live status (from cron_runs).
    last_run_at: str | None
    last_status: str | None
    last_duration_ms: int | None
    last_error: str | None
    next_run_at: str | None
    last_24h: CronCounts


class CronListResponse(APIBaseModel):
    items: list[CronScheduleItem]
    site_timezone: str


class UpdateCronScheduleRequest(APIBaseModel):
    enabled: bool
    kind: str = Field(pattern="^(interval|daily)$")
    interval_minutes: int = Field(ge=1, le=1440 * 7)
    daily_time: str = Field(pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
