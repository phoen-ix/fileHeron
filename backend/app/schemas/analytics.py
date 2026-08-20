"""Response models for `/api/admin/analytics`.

The route answered `-> dict` with no `response_model`, so the six frontend
interfaces backing the analytics dashboard had no schema to check against.
Field-for-field from `services/analytics.compute_analytics`.
"""
from __future__ import annotations

from .common import APIBaseModel


class AnalyticsDayPoint(APIBaseModel):
    date: str
    count: int


class AnalyticsStoragePoint(APIBaseModel):
    date: str
    storage_bytes: int
    files_clean: int
    files_infected: int
    files_total: int


class AnalyticsTopUploader(APIBaseModel):
    user_id: int
    display_name: str
    email: str
    bytes: int


class AnalyticsTopShare(APIBaseModel):
    share_id: str
    subject: str | None = None
    downloads: int


class AnalyticsQuotaWarning(APIBaseModel):
    user_id: int
    display_name: str
    email: str
    used_bytes: int
    quota_bytes: int
    pct: float


class AnalyticsResponse(APIBaseModel):
    days: int
    range: dict[str, str]
    storage_trend: list[AnalyticsStoragePoint]
    storage_as_of: str | None = None
    shares_created: list[AnalyticsDayPoint]
    downloads: list[AnalyticsDayPoint]
    av_quarantines: list[AnalyticsDayPoint]
    file_states: dict[str, int]
    top_uploaders: list[AnalyticsTopUploader]
    top_shares: list[AnalyticsTopShare]
    quota_warnings: list[AnalyticsQuotaWarning]
