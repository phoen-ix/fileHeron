"""Share endpoints — list, get, create, revoke, expire-now, patch-expiry."""
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
    expires_at_never: bool = False,
    public_link: Optional[dict] = None,
) -> ShareResponse:
    """Create the share envelope. Files are added in a separate step
    (POST /api/uploads/direct or the TUS init+upload flow).

    v0.3.0 extras (matching SPA create-share parity):

    - ``expires_at_never=True`` sends ``expires_at: null`` so the
      share is never auto-deleted (v1.1.4 backend semantics). Mutually
      exclusive with ``expires_at``.
    - ``public_link`` is an inline ``{password?, download_limit?,
      notify_on_download}`` dict; the server returns the plaintext URL
      ONCE in ``ShareResponse.public_link.url``.
    """
    if expires_at_never and expires_at is not None:
        raise ValueError(
            "Pass either expires_at or expires_at_never=True, not both."
        )
    body: dict = {
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
    if expires_at_never:
        body["expires_at"] = None
    elif expires_at is not None:
        body["expires_at"] = expires_at.isoformat()
    if public_link is not None:
        body["public_link"] = public_link
    out = api.request_or_raise("POST", "/api/shares", json=body, expected=201)
    return ShareResponse.model_validate(out)


def delete_share(api: ApiClient, share_id: str) -> None:
    api.request_or_raise(
        "DELETE", f"/api/shares/{share_id}", expected=204
    )


# v0.2.0 share-manager actions ------------------------------------------------
#
# All three return the up-to-date ShareResponse so the caller can refresh
# the dialog state from one response — no follow-up GET needed.


def revoke_share(api: ApiClient, share_id: str) -> None:
    """Revoke an active share. Files become inaccessible to recipients;
    audit row `share_revoked` is written server-side. 204 No Content.

    Alias for ``delete_share`` — the backend routes DELETE /shares/{id}
    to ``services.share.revoke_share`` (no hard-delete). Kept as a
    separate name because the SPA-mirrored UI button reads "Revoke".
    """
    delete_share(api, share_id)


def expire_share_now(api: ApiClient, share_id: str) -> ShareResponse:
    """Force-expire the share immediately. Flips state to ``expired``,
    sets ``expires_at = now()``, hard-deletes the file bytes from disk.
    Audit row ``share_expired`` with ``{via: "owner_action"}``."""
    out = api.request_or_raise("POST", f"/api/shares/{share_id}/expire")
    return ShareResponse.model_validate(out)


def patch_share_expiry(
    api: ApiClient,
    share_id: str,
    *,
    expires_at: Optional[datetime] = None,
    clear: bool = False,
) -> ShareResponse:
    """PATCH ``/api/shares/{id}`` with expiry semantics:

    - ``clear=True`` → ``expires_at_clear: true`` (share becomes
      never-expire, v1.1.4 semantics). Mutually exclusive with
      supplying ``expires_at`` (server returns 400 on both).
    - ``expires_at=<datetime>`` → replace.
    - both unset → no-op (caller should not call us in that case).
    """
    if clear and expires_at is not None:
        raise ValueError(
            "Pass either expires_at or clear=True, not both."
        )
    body: dict = {}
    if clear:
        body["expires_at_clear"] = True
    elif expires_at is not None:
        body["expires_at"] = expires_at.isoformat()
    out = api.request_or_raise("PATCH", f"/api/shares/{share_id}", json=body)
    return ShareResponse.model_validate(out)
