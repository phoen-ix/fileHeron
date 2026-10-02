"""Secret-request endpoints (server v2.24.0): ask someone for a secret,
answer a request made of you, cancel, links.

The answer's text goes OUT in ``answer_secret_request``'s body and comes back
only as an ordinary secret (``secrets.reveal_secret``). Passphrases always
travel in a request BODY.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import (
    AnswerSecretRequestResponse,
    SecretRequestLinksResponse,
    SecretRequestListResponse,
    SecretRequestResponse,
)
from .client import ApiClient
from .shares import _expiry_to_utc_iso


def list_secret_requests(
    api: ApiClient,
    *,
    box: str = "mine",
    q: str = "",
    states: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 200,
) -> SecretRequestListResponse:
    """``box`` is ``mine`` (asked by me) or ``asked`` (asked of me)."""
    params: dict = {"box": box, "page": page, "page_size": page_size}
    if q:
        params["q"] = q
    if states:
        params["state"] = list(states)
    out = api.request_or_raise("GET", "/api/secret-requests", params=params)
    return SecretRequestListResponse.model_validate(out)


def get_secret_request(api: ApiClient, request_id: str) -> SecretRequestResponse:
    out = api.request_or_raise("GET", f"/api/secret-requests/{request_id}")
    return SecretRequestResponse.model_validate(out)


def create_secret_request(
    api: ApiClient,
    *,
    label: str,
    expires_at: datetime,
    note: Optional[str] = None,
    answer_max_views: Optional[int] = None,
    answer_expires_in_sec: Optional[int] = None,
    passphrase: Optional[str] = None,
    user_ids: Optional[list[int]] = None,
    group_ids: Optional[list[int]] = None,
    emails: Optional[list[str]] = None,
    create_link: bool = False,
) -> SecretRequestResponse:
    """Ask for a secret. ``expires_at`` is how long the request stays open;
    the ``answer_*`` limits govern the answer once it arrives. The returned
    ``link_url`` is set only when ``create_link`` made one."""
    body: dict = {
        "label": label,
        "note": note or None,
        "expires_at": _expiry_to_utc_iso(expires_at),
        "answer_max_views": answer_max_views,
        "answer_expires_in_sec": answer_expires_in_sec,
        "passphrase": passphrase or None,
        "recipients": {
            "user_ids": list(user_ids or []),
            "group_ids": list(group_ids or []),
            "emails": list(emails or []),
        },
        "create_link": create_link,
    }
    out = api.request_or_raise("POST", "/api/secret-requests", json=body, expected=201)
    return SecretRequestResponse.model_validate(out)


def answer_secret_request(
    api: ApiClient,
    request_id: str,
    *,
    content: str,
    passphrase: Optional[str] = None,
) -> AnswerSecretRequestResponse:
    """Answer a request made of me. ``passphrase`` is the answerer's own
    optional layer; the requester needs it as well to open the answer."""
    body = {"content": content, "passphrase": passphrase or None}
    out = api.request_or_raise(
        "POST", f"/api/secret-requests/{request_id}/answer", json=body,
    )
    return AnswerSecretRequestResponse.model_validate(out)


def cancel_secret_request(api: ApiClient, request_id: str) -> SecretRequestResponse:
    out = api.request_or_raise("POST", f"/api/secret-requests/{request_id}/cancel")
    return SecretRequestResponse.model_validate(out)


def get_secret_request_links(
    api: ApiClient, request_id: str,
) -> SecretRequestLinksResponse:
    """The requester's links again. The server records every call."""
    out = api.request_or_raise("GET", f"/api/secret-requests/{request_id}/links")
    return SecretRequestLinksResponse.model_validate(out)
