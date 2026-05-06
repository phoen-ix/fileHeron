"""Admin share-defaults settings schemas (post-Phase 10)."""
from __future__ import annotations

from .common import APIBaseModel


class ShareDefaultsResponse(APIBaseModel):
    """Returned by GET /api/admin/settings/share-defaults."""
    notify_recipients_default: bool


class UpdateShareDefaultsRequest(APIBaseModel):
    notify_recipients_default: bool
