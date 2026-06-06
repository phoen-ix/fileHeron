"""Schemas for admin-editable legal pages (imprint + privacy), per locale."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel

# Generous cap - legal texts can be long, but bound it to keep the kv row sane.
_MAX = 50_000


class LegalDoc(APIBaseModel):
    enabled: bool = False
    en: str = ""
    de: str = ""


class LegalSettingsResponse(APIBaseModel):
    imprint: LegalDoc
    privacy: LegalDoc


class LegalDocUpdate(APIBaseModel):
    enabled: bool
    en: str = Field(default="", max_length=_MAX)
    de: str = Field(default="", max_length=_MAX)


class UpdateLegalSettingsRequest(APIBaseModel):
    imprint: LegalDocUpdate
    privacy: LegalDocUpdate


class LegalContentResponse(APIBaseModel):
    """Public per-kind content: sanitised HTML for both locales."""
    enabled: bool
    html_en: str = ""
    html_de: str = ""
