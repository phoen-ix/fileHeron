"""Group listing - backs the v0.3.0 recipient picker.

Backend endpoint: GET /api/groups/recipient-targets - returns the
groups the caller can target in a share (role-scoped server-side:
admins see all; employees see their memberships + every
company_inbox; clients see only company_inbox groups)."""
from __future__ import annotations

from .client import ApiClient
from ..models import GroupListResponse


def list_recipient_groups(api: ApiClient) -> GroupListResponse:
    out = api.request_or_raise("GET", "/api/groups/recipient-targets")
    return GroupListResponse.model_validate(out)
