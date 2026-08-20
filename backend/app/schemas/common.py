"""Shared Pydantic base classes."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class APIBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class OkResponse(APIBaseModel):
    """The bare acknowledgement a dozen routes answer with. Declared so the
    routes appear in the OpenAPI schema at all - `-> dict` produces nothing a
    client or a contract test can read."""
    ok: bool = True


class RevokedCountResponse(APIBaseModel):
    revoked: int


class SignedUrlResponse(APIBaseModel):
    """A short-lived `?dt=` URL. `<a href>` cannot carry a bearer token, so the
    SPA mints one of these first (see utils/download_token)."""
    url: str


class PaginatedResponse(APIBaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool
