"""Admin maintenance-mode settings schemas (v1.34.0)."""
from __future__ import annotations

from .common import APIBaseModel


class MaintenanceSettingsResponse(APIBaseModel):
    """Returned by GET/PUT /api/admin/settings/maintenance."""
    enabled: bool
    message: str
    active_uploads: int
    active_downloads: int


class UpdateMaintenanceSettingsRequest(APIBaseModel):
    enabled: bool
    message: str | None = None
