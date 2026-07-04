"""Share endpoints - list, get, create, revoke, expire-now, patch-expiry."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .client import ApiClient
from ..models import ShareListResponse, ShareResponse


def _expiry_to_utc_iso(dt: datetime) -> str:
    """Serialize a share-expiry datetime as an offset-bearing UTC ISO string.

    The expiry picker hands us a *naive* datetime that is the user's local
    wall-clock choice. The backend reads a tz-less ISO string as UTC, so a naive
    value would be off by the machine's UTC offset; attach the local offset (or
    keep an already-aware value's) and convert to UTC before sending.
    """
    aware = dt if dt.tzinfo is not None else dt.astimezone()
    return aware.astimezone(timezone.utc).isoformat()


def list_shares(
    api: ApiClient,
    *,
    box: str = "outbox",
    q: str = "",
    states: Optional[list[str]] = None,
    page: int = 1,
    page_size: int = 50,
    sort: Optional[str] = None,
    direction: Optional[str] = None,
    sender_user_id: Optional[int] = None,
    recipient_user_id: Optional[int] = None,
    recipient_group_id: Optional[int] = None,
) -> ShareListResponse:
    """v0.7.2: optional sort + party-filter params matching the SPA's
    GET /api/shares query shape.

    - ``sort`` ∈ ``created_at`` (default server-side) / ``expires_at`` /
      ``subject``. ``direction`` ∈ ``asc`` / ``desc`` (default ``desc``).
    - Party filters (``sender_user_id`` for inbox, ``recipient_user_id`` /
      ``recipient_group_id`` for outbox) narrow to a single party. Mutually
      consistent with whatever the panel UI exposes.
    """
    params: dict = {"box": box, "page": page, "page_size": page_size}
    if q:
        params["q"] = q
    if states:
        params["state"] = states  # httpx serialises list-valued params correctly
    if sort is not None:
        params["sort"] = sort
    if direction is not None:
        params["direction"] = direction
    if sender_user_id is not None:
        params["sender_user_id"] = sender_user_id
    if recipient_user_id is not None:
        params["recipient_user_id"] = recipient_user_id
    if recipient_group_id is not None:
        params["recipient_group_id"] = recipient_group_id
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
    download_limit: Optional[int] = None,
    public_link: Optional[dict] = None,
) -> ShareResponse:
    """Create the share envelope. Files are added in a separate step
    (POST /api/uploads/direct or the TUS init+upload flow).

    v0.3.0 extras (matching SPA create-share parity):

    - ``expires_at_never=True`` sends ``expires_at: null`` so the
      share is never auto-deleted (v1.1.4 backend semantics). Mutually
      exclusive with ``expires_at``.
    - ``download_limit`` (v1.1.0 backend feature, exposed in client
      v0.4.26): per-share total download cap for AUTHENTICATED
      recipients. Server stores ``downloads_remaining`` and decrements
      atomically per download. NULL / None = unlimited. Separate from
      and additive to the public-link download_limit.
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
        body["expires_at"] = _expiry_to_utc_iso(expires_at)
    if download_limit is not None:
        body["download_limit"] = download_limit
    if public_link is not None:
        body["public_link"] = public_link
    out = api.request_or_raise("POST", "/api/shares", json=body, expected=201)
    return ShareResponse.model_validate(out)


def expire_share_now(api: ApiClient, share_id: str) -> ShareResponse:
    """Force-expire the share immediately. Flips state to ``expired``,
    sets ``expires_at = now()``, hard-deletes the file bytes from disk.
    Audit row ``share_expired`` with ``{via: "owner_action"}``."""
    out = api.request_or_raise("POST", f"/api/shares/{share_id}/expire")
    return ShareResponse.model_validate(out)


def register_files_added(
    api: ApiClient,
    share_id: str,
    *,
    notify: bool,
    file_ids: list[str],
) -> ShareResponse:
    """v1.12.0: the owner's batch-complete signal after uploading more files
    into an active share (the files were already attached by the upload
    pipeline). Records a share-level audit row and, when ``notify``, re-notifies
    the share's recipients. Returns the refreshed share. Owner + active gated
    server-side (403 FORBIDDEN / 409 SHARE_NOT_ACTIVE)."""
    out = api.request_or_raise(
        "POST",
        f"/api/shares/{share_id}/files-added",
        json={"notify": notify, "file_ids": list(file_ids)},
    )
    return ShareResponse.model_validate(out)


def get_public_link(api: ApiClient, share_id: str) -> Optional[dict]:
    """Return the public-link metadata for an owned share (incl. the
    plaintext URL via the encrypted-token column shipped in
    migration 202605031400), or ``None`` if the share has no public
    link / the request fails. Backend enforces owner+admin auth on
    GET ``/api/shares/{id}/public-link``.

    Returns a raw dict rather than a typed model - the desktop client
    only reads a few fields and a permissive shape lets backend
    schema changes land without a client release. Notable keys:
    ``url`` (Optional[str] - None for legacy rows where the token
    wasn't encrypted), ``has_password``, ``download_limit``,
    ``downloads_remaining``, ``locked_until``, ``revoked_at``.
    """
    try:
        return api.request_or_raise(
            "GET", f"/api/shares/{share_id}/public-link",
        )
    except Exception:
        return None


def patch_share_download_limit(
    api: ApiClient,
    share_id: str,
    *,
    limit: Optional[int] = None,
    clear: bool = False,
) -> ShareResponse:
    """v0.7.1: edit a share's per-recipient download budget.

    Same backend route as ``patch_share_expiry`` (``PATCH /api/shares/{id}``)
    - body keys are ``download_limit`` (int > 0) and
    ``download_limit_clear`` (true → no limit, mutually exclusive with
    ``download_limit``).
    """
    if clear and limit is not None:
        raise ValueError(
            "Pass either limit or clear=True, not both."
        )
    body: dict = {}
    if clear:
        body["download_limit_clear"] = True
    elif limit is not None:
        body["download_limit"] = limit
    out = api.request_or_raise("PATCH", f"/api/shares/{share_id}", json=body)
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
        body["expires_at"] = _expiry_to_utc_iso(expires_at)
    out = api.request_or_raise("PATCH", f"/api/shares/{share_id}", json=body)
    return ShareResponse.model_validate(out)


# ---------------------------------------------------------------------------
# Backward-compat for API-token scripts (UIs collapsed in v0.6.1)
#
# The desktop client + SPA UIs both ship a single "End share" button that
# routes to ``expire_share_now`` (state → expired, files hard-deleted). The
# wrappers below stay because:
#
#   - External scripts using API tokens may already call them.
#   - Some flows still want the soft-revoke semantics (state → revoked, files
#     stay on disk) that the backend's DELETE /shares/{id} preserves.
#
# Neither is called from any UI module in this package. Don't wire either
# back into the UI without revisiting v0.6.1's "End share" decision.
# ---------------------------------------------------------------------------


def delete_share(api: ApiClient, share_id: str) -> None:
    api.request_or_raise(
        "DELETE", f"/api/shares/{share_id}", expected=204
    )


def revoke_share(api: ApiClient, share_id: str) -> None:
    """Revoke an active share. Files become inaccessible to recipients
    but stay on disk; audit row ``share_revoked`` is written server-side.
    204 No Content. Alias for ``delete_share`` (the backend routes
    DELETE /shares/{id} to ``services.share.revoke_share``)."""
    delete_share(api, share_id)
