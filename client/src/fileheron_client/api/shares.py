"""Share endpoints — list, get, create, revoke."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .client import ApiClient
from ..models import ShareListResponse, ShareResponse


def list_shares(
    api: ApiClient,
    *,
    box: str = "outbox",
    q: str = "",
    states: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 50,
) -> ShareListResponse:
    params: dict = {"box": box, "page": page, "page_size": page_size}
    if q:
        params["q"] = q
    if states:
        params["state"] = states  # httpx serialises list-valued params correctly
    out = api.request_or_raise("GET", "/api/shares", params=params)
    return ShareListResponse.model_validate(out)


def get_share(api: ApiClient, share_id: str) -> ShareResponse:
    out = api.request_or_raise("GET", f"/api/shares/{share_id}")
    return ShareResponse.model_validate(out)


def create_share(
    api: ApiClient,
    *,
    kind: str = "outbound",
    recipient_user_ids: Optional[list[int]] = None,
    recipient_group_ids: Optional[list[int]] = None,
    subject: Optional[str] = None,
    message: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> ShareResponse:
    """Create the share envelope. Files are added in a separate step
    (POST /api/uploads/direct or the TUS init+upload flow)."""
    body = {
        "kind": kind,
        "recipients": {
            "user_ids": list(recipient_user_ids or []),
            "group_ids": list(recipient_group_ids or []),
        },
    }
    if subject is not None:
        body["subject"] = subject
    if message is not None:
        body["message"] = message
    if expires_at is not None:
        body["expires_at"] = expires_at.isoformat()
    out = api.request_or_raise("POST", "/api/shares", json=body, expected=201)
    return ShareResponse.model_validate(out)


def delete_share(api: ApiClient, share_id: str) -> None:
    api.request_or_raise(
        "DELETE", f"/api/shares/{share_id}", expected=204
    )
