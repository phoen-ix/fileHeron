"""User search - backs the v0.3.0 recipient picker.

Backend endpoint: GET /api/users/search?q=<needle> - returns the union
of users the caller can address as a recipient, filtered by substring
on display_name + email. Role-scoped server-side (clients see
connected employees only; employees see all employees + connected
clients; admins see everyone)."""
from __future__ import annotations

from .client import ApiClient
from ..models import UserSearchResponse


def search_users(api: ApiClient, q: str = "") -> UserSearchResponse:
    """Returns up to N matching users (server caps the page size).
    Empty ``q`` returns the full visible set."""
    out = api.request_or_raise("GET", "/api/users/search", params={"q": q})
    return UserSearchResponse.model_validate(out)
