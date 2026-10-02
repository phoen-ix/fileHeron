"""Secret endpoints (server v2.24.0): list, read, send, reveal, burn, links.

A secret's text crosses the wire in exactly two places: OUT in
``create_secret``'s body and IN from ``reveal_secret``. Every other call is
metadata. Passphrases always travel in a request BODY, never in a URL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import (
    RevealSecretResponse,
    SecretLinksResponse,
    SecretListResponse,
    SecretResponse,
)
from .client import ApiClient
from .shares import _expiry_to_utc_iso


def list_secrets(
    api: ApiClient,
    *,
    box: str = "received",
    q: str = "",
    states: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 200,
) -> SecretListResponse:
    """``box`` is ``received`` or ``sent``; ``states`` filters on the secret's
    state (sent as repeated ``state=`` params)."""
    params: dict = {"box": box, "page": page, "page_size": page_size}
    if q:
        params["q"] = q
    if states:
        params["state"] = list(states)
    out = api.request_or_raise("GET", "/api/secrets", params=params)
    return SecretListResponse.model_validate(out)


def get_secret(api: ApiClient, secret_id: str) -> SecretResponse:
    out = api.request_or_raise("GET", f"/api/secrets/{secret_id}")
    return SecretResponse.model_validate(out)


def create_secret(
    api: ApiClient,
    *,
    content: str,
    label: Optional[str] = None,
    passphrase: Optional[str] = None,
    max_views: Optional[int] = None,
    view_scope: str = "per_person",
    expires_at: Optional[datetime] = None,
    user_ids: Optional[list[int]] = None,
    group_ids: Optional[list[int]] = None,
    emails: Optional[list[str]] = None,
    create_link: bool = False,
    notify_on_view: bool = False,
    burn_on_failures: bool = False,
) -> SecretResponse:
    """Send a secret. ``expires_at=None`` means no expiry (the server then
    needs ``max_views`` and caps the lifetime itself). The returned
    ``link_url`` is set only when ``create_link`` made one."""
    body: dict = {
        "content": content,
        "label": label or None,
        "passphrase": passphrase or None,
        "max_views": max_views,
        "view_scope": view_scope,
        "expires_at": _expiry_to_utc_iso(expires_at) if expires_at is not None else None,
        "recipients": {
            "user_ids": list(user_ids or []),
            "group_ids": list(group_ids or []),
            "emails": list(emails or []),
        },
        "create_link": create_link,
        "notify_on_view": notify_on_view,
        "burn_on_failures": burn_on_failures and bool(passphrase),
    }
    out = api.request_or_raise("POST", "/api/secrets", json=body, expected=201)
    return SecretResponse.model_validate(out)


def reveal_secret(
    api: ApiClient,
    secret_id: str,
    *,
    passphrase: Optional[str] = None,
    request_passphrase: Optional[str] = None,
) -> RevealSecretResponse:
    """Spend one view and return the text. ``request_passphrase`` is the one
    the requester set on their own request (an answer only). A wrong
    passphrase is a 403 that spends no view."""
    body = {
        "passphrase": passphrase or None,
        "request_passphrase": request_passphrase or None,
    }
    out = api.request_or_raise("POST", f"/api/secrets/{secret_id}/reveal", json=body)
    return RevealSecretResponse.model_validate(out)


def burn_secret(api: ApiClient, secret_id: str) -> SecretResponse:
    """End the secret now (the sender, or the requester of an answer)."""
    out = api.request_or_raise("POST", f"/api/secrets/{secret_id}/burn")
    return SecretResponse.model_validate(out)


def get_secret_links(api: ApiClient, secret_id: str) -> SecretLinksResponse:
    """The sender's links again (each address's, and the link). The server
    records every call."""
    out = api.request_or_raise("GET", f"/api/secrets/{secret_id}/links")
    return SecretLinksResponse.model_validate(out)
