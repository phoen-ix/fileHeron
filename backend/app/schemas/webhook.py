"""Webhook admin schemas (v1.19.0)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from ..services import webhook as webhook_svc
from .common import APIBaseModel


def _validate_url(v: str) -> str:
    v = v.strip()
    if not (v.startswith("https://") or v.startswith("http://")):
        raise ValueError("url must start with http:// or https://")
    if len(v) > 2048:
        raise ValueError("url too long")
    return v


def _validate_events(v: list[str]) -> list[str]:
    if not v:
        raise ValueError("at least one event is required")
    allowed = set(webhook_svc.WEBHOOK_EVENTS) | {"*"}
    bad = [e for e in v if e not in allowed]
    if bad:
        raise ValueError(f"unknown event(s): {', '.join(bad)}")
    return v


class CreateWebhookRequest(APIBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: str
    event_types: list[str] = Field(default_factory=list)

    _v_url = field_validator("url")(_validate_url)
    _v_events = field_validator("event_types")(_validate_events)


class UpdateWebhookRequest(APIBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = None
    event_types: list[str] | None = None
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def _vu(cls, v: str | None) -> str | None:
        return _validate_url(v) if v is not None else None

    @field_validator("event_types")
    @classmethod
    def _ve(cls, v: list[str] | None) -> list[str] | None:
        return _validate_events(v) if v is not None else None


class WebhookResponse(APIBaseModel):
    id: int
    name: str
    url: str
    event_types: list[str]
    active: bool
    secret_set: bool
    created_at: datetime


class WebhookCreateResponse(WebhookResponse):
    """Adds the plaintext signing secret — returned ONCE, on create / rotate."""
    secret: str


class WebhookDeliveryResponse(APIBaseModel):
    id: int
    event_type: str
    status: str
    response_code: int | None
    attempts: int
    error: str | None
    created_at: datetime
    delivered_at: datetime | None
