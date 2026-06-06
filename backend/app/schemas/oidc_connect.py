"""Schemas for the authed OIDC connect flow (Phase 10)."""
from __future__ import annotations

from ..models.oidc_provider import OIDCPreset
from .common import APIBaseModel


class ConnectStartResponse(APIBaseModel):
    redirect_url: str


class OIDCLinkItem(APIBaseModel):
    provider_id: str
    provider_name: str
    preset: OIDCPreset
    sub_hint: str  # truncated subject for display ("…1234")


class OIDCLinkResponse(APIBaseModel):
    """Returned by GET /api/account/oidc/links - at most one link per
    user, reflecting the locked single-provider-per-user design."""
    link: OIDCLinkItem | None
