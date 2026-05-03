"""Admin home-page settings schemas (post-Phase 10)."""
from __future__ import annotations

from .common import APIBaseModel


class HomePageSettingsResponse(APIBaseModel):
    """Returned by GET /api/admin/settings/home-page."""
    enabled: bool


class UpdateHomePageSettingsRequest(APIBaseModel):
    enabled: bool
