"""Admin file-preview settings schemas (v1.23.0)."""
from __future__ import annotations

from .common import APIBaseModel


class FilePreviewSettingsResponse(APIBaseModel):
    """Returned by GET /api/admin/settings/file-preview."""
    enabled: bool


class UpdateFilePreviewSettingsRequest(APIBaseModel):
    enabled: bool
