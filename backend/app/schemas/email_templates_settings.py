"""Admin email-template editor schemas (v1.25.0)."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class PlaceholderMeta(APIBaseModel):
    token: str
    label: str
    description: str
    kind: str  # text | url | datetime
    required: bool


class EmailTemplateLocale(APIBaseModel):
    code: str
    label: str


class EmailTemplateItem(APIBaseModel):
    slug: str
    group: str
    locale: str
    has_override: bool
    # Effective subject (override if set, else built-in default).
    subject: str
    # The override body HTML, or "" when none exists yet (the editor seeds from
    # the built-in default HTML in that case - see `default_body`).
    body_html: str
    default_subject: str
    default_body: str
    placeholders: list[PlaceholderMeta]


class EmailTemplateSummaryItem(APIBaseModel):
    slug: str
    group: str
    # locale code -> has_override
    has_override: dict[str, bool]


class EmailTemplatesListResponse(APIBaseModel):
    locales: list[EmailTemplateLocale]
    groups: list[str]
    items: list[EmailTemplateSummaryItem]
    placeholders: dict[str, list[PlaceholderMeta]]


class UpdateEmailTemplateRequest(APIBaseModel):
    # None/"" subject ⇒ inherit the built-in subject.
    subject: str | None = Field(default=None, max_length=512)
    body_html: str = Field(..., min_length=1, max_length=50_000)


class PreviewEmailTemplateRequest(APIBaseModel):
    subject: str | None = Field(default=None, max_length=512)
    body_html: str = Field(..., max_length=50_000)


class PreviewEmailTemplateResponse(APIBaseModel):
    subject: str
    text: str
    html: str


class TestSendEmailTemplateRequest(APIBaseModel):
    # The (possibly unsaved) edits to render. Recipient is forced to the
    # requesting admin's own address server-side.
    subject: str | None = Field(default=None, max_length=512)
    body_html: str = Field(..., max_length=50_000)


class TestSendEmailTemplateResponse(APIBaseModel):
    ok: bool
    error_class: str | None = None
    error_message: str | None = None
    smtp_code: int | None = None
    hint: str | None = None
    sent_to: str | None = None
