"""Programmatic API tokens.

Wire format: ``fh_<8-hex-prefix>_<43-char-base64url-secret>``.
- prefix is 4 bytes of crypto-random rendered as 8 hex chars (indexed unique
  in DB; allows lookup without iterating).
- secret is 32 bytes of crypto-random urlsafe-base64 (rstrip "=" → 43 chars).

Backend stores: prefix, last4 (display only), secret_hash (SHA-256 of the
secret half - high-entropy random, no Argon2 needed).

Verify:
    parse "fh_<prefix>_<secret>"
    SELECT row by prefix
    constant-time compare sha256(secret) to row.secret_hash
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.api_token import ApiToken
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.crypto import constant_time_equals, sha256_hex
from ..utils.timeutil import utc_now
from .audit import record_audit_event

TOKEN_PREFIX_BYTES = 4  # 8 hex chars
TOKEN_SECRET_BYTES = 32  # 43 b64url chars (no padding)

# Granularity for the `last_used_at` write - one update per minute is plenty for
# a human-facing "last used" while avoiding an UPDATE on every single request.
LAST_USED_THROTTLE_SEC = 60

# Canonical per-token scopes (least privilege). A token's stored `scopes` is a
# JSON array of a SUBSET of these; NULL = unrestricted (full access). Each maps
# to a `require_scope(...)` gate on the token-reachable routes - keep this list
# and the route annotations in lockstep (the deny-by-default backstop test
# fails if a get_actor route is left ungated).
SCOPES: frozenset[str] = frozenset(
    {
        "files:upload",       # POST /uploads/init, /uploads/direct
        "shares:create",      # POST /api/shares
        "shares:add_files",   # POST /api/shares/{id}/files-added
        "shares:read",        # list/read shares, recipients, recipient-targets
        "shares:manage",      # PATCH/expire/bulk-expire/DELETE/approve/reject/resubmit
        "files:download",     # download/preview/zip + the *-url minters
        "files:delete",       # DELETE /api/files/{id}
        # Reading the link is a SEPARATE grant from creating one: the read
        # returns the DECRYPTED plaintext URL (and a QR of it), which is an
        # anonymous, password-free path to the file bytes. Gating that on
        # shares:read let a metadata-only token exfiltrate every file its
        # owner had shared, with no files:download (audit 2026-07-30).
        "public_links:read",  # read back an existing public link's URL/QR
        "public_links:write", # create/revoke a public link (+ inline-on-create)
        "recipients:search",  # GET /api/users/search, /api/users/me/connections
    }
)


def normalize_scopes(raw: list[str] | None) -> str | None:
    """Validate + canonicalise a requested scope list for storage.

    - ``None`` (field omitted / null) -> ``None`` => unrestricted (full access).
    - ``[]`` (empty list) -> 400 INVALID_SCOPE: an empty restricted set is a
      token that can reach nothing but the any-token routes - almost always a
      mistake. Callers wanting full access omit the field.
    - any unknown scope -> 400 INVALID_SCOPE (details lists the offenders).
    - otherwise -> a JSON array of the de-duped, sorted scope names.
    """
    if raw is None:
        return None
    requested = {s.strip() for s in raw if s and s.strip()}
    if not requested:
        raise AppError(
            400,
            "INVALID_SCOPE",
            "Scope list cannot be empty; omit it for an unrestricted token.",
        )
    unknown = sorted(requested - SCOPES)
    if unknown:
        raise AppError(
            400, "INVALID_SCOPE", "Unknown scope(s).", details={"unknown": unknown}
        )
    return json.dumps(sorted(requested))


def token_scope_set(record: ApiToken) -> set[str] | None:
    """Parse a token's stored scopes. ``None`` => unrestricted (full access)."""
    if record.scopes is None:
        return None
    return set(json.loads(record.scopes))


def _gen_prefix() -> str:
    return secrets.token_hex(TOKEN_PREFIX_BYTES)


def _gen_secret() -> str:
    raw = secrets.token_bytes(TOKEN_SECRET_BYTES)
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def normalize_expiry(expires_at: datetime | None) -> datetime | None:
    """Validate + normalise an optional API-token expiry to naive UTC (the
    storage convention), mirroring services/share.py::create_share. None =
    never expires. Refuses a past timestamp with 400 INVALID_EXPIRY."""
    if expires_at is None:
        return None
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires_at < utc_now():
        raise AppError(400, "INVALID_EXPIRY", "Expiry must be in the future.")
    return expires_at


def create_token(
    db: Session,
    *,
    owner: User,
    name: str,
    expires_at: datetime | None = None,
    scopes: str | None = None,
) -> tuple[ApiToken, str]:
    """Returns (record, plaintext_token). The plaintext is shown to the user
    once; only the SHA-256 hash is stored. ``expires_at`` NULL = never expires.
    ``scopes`` is the already-normalised JSON string (or None = unrestricted)
    from :func:`normalize_scopes`."""
    prefix = _gen_prefix()
    secret = _gen_secret()
    plaintext = f"fh_{prefix}_{secret}"

    record = ApiToken(
        owner_user_id=owner.id,
        name=name.strip()[:120] or "(unnamed)",
        prefix=prefix,
        last4=secret[-4:],
        secret_hash=sha256_hex(secret),
        expires_at=expires_at,
        scopes=scopes,
    )
    db.add(record)
    db.flush()
    return record, plaintext


def parse_bearer(token_str: str) -> tuple[str, str] | None:
    """Returns (prefix, secret) or None if the string isn't an API token."""
    if not token_str.startswith("fh_"):
        return None
    parts = token_str.split("_", 2)
    if len(parts) != 3:
        return None
    _, prefix, secret = parts
    if len(prefix) != TOKEN_PREFIX_BYTES * 2:
        return None
    if len(secret) < 40:
        return None
    return prefix, secret


def verify_token(db: Session, *, token_str: str) -> ApiToken:
    """Looks up the token by prefix, constant-time-compares the hashed
    secret, returns the record if valid and not revoked. Raises AppError
    otherwise (with the same code so callers don't have to switch on
    different error shapes for missing/wrong/revoked)."""
    parsed = parse_bearer(token_str)
    if parsed is None:
        raise AppError(401, "INVALID_API_TOKEN", "Invalid API token.")
    prefix, secret = parsed

    record = db.query(ApiToken).filter(ApiToken.prefix == prefix).one_or_none()
    if record is None:
        raise AppError(401, "INVALID_API_TOKEN", "Invalid API token.")
    if record.revoked_at is not None:
        raise AppError(401, "INVALID_API_TOKEN", "API token has been revoked.")
    if record.expires_at is not None and utc_now() > record.expires_at:
        raise AppError(401, "API_TOKEN_EXPIRED", "API token has expired.")
    if record.disabled_at is not None:
        # Distinct code so admin/UX can tell "temporarily off" from "gone".
        raise AppError(401, "API_TOKEN_DISABLED", "API token is disabled.")
    if not constant_time_equals(record.secret_hash, sha256_hex(secret)):
        raise AppError(401, "INVALID_API_TOKEN", "Invalid API token.")

    _record_last_used(db, record)
    return record


def _record_last_used(db: Session, record: ApiToken) -> None:
    """Persist token usage. Most API-token requests are GETs, and ``get_db``
    never commits (it rolls back on close) - so a bare ``flush()`` here was
    discarded when the request ended, and ``last_used_at`` only ever advanced
    on write endpoints that happened to commit. Commit it instead: this runs in
    the auth dependency, before the endpoint body, so only this update is
    pending. Throttled to one write per minute to avoid an UPDATE on every
    request; best-effort so a write failure never blocks authentication."""
    now = utc_now()
    last = record.last_used_at
    if last is not None and (now - last).total_seconds() < LAST_USED_THROTTLE_SEC:
        return
    record.last_used_at = now
    try:
        db.commit()
    except Exception:
        db.rollback()


def list_tokens(db: Session, *, owner: User) -> list[ApiToken]:
    return (
        db.query(ApiToken)
        .filter(ApiToken.owner_user_id == owner.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
        .all()
    )


def revoke_token(db: Session, *, owner: User, token_id: int) -> None:
    record = (
        db.query(ApiToken)
        .filter(ApiToken.id == token_id, ApiToken.owner_user_id == owner.id)
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "TOKEN_NOT_FOUND", "API token not found.")
    if record.revoked_at is None:
        record.revoked_at = utc_now()
    # Once revoked, disabled is moot - clear it for cleanliness.
    record.disabled_at = None
    db.flush()


# ---------------------------------------------------------------------------
# Policy gate (post-Phase 10) - shared logic lives in services/policy_gate.
# ---------------------------------------------------------------------------


def _policy_keys() -> dict[str, str]:
    from . import settings as settings_svc

    return {
        "mode_key": settings_svc.Keys.API_TOKEN_POLICY_MODE,
        "users_key": settings_svc.Keys.API_TOKEN_ALLOWED_USERS,
        "groups_key": settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS,
    }


def _resolve_policy(db: Session) -> tuple[str, list[int], list[int]]:
    from . import policy_gate

    return policy_gate.resolve_policy(db, **_policy_keys())


def is_allowed_to_create(db: Session, user: User) -> bool:
    """Returns True if the user can mint an API token under the current
    policy. Admin always passes (operator escape hatch)."""
    from . import policy_gate

    return policy_gate.is_allowed(db, user, **_policy_keys())


# ---------------------------------------------------------------------------
# Admin token operations (post-Phase 10)
# ---------------------------------------------------------------------------


def _get_token_or_404(db: Session, token_id: int) -> ApiToken:
    record = db.query(ApiToken).filter(ApiToken.id == token_id).one_or_none()
    if record is None:
        raise AppError(404, "TOKEN_NOT_FOUND", "API token not found.")
    return record


def disable_token(
    db: Session, *, actor: User, token_id: int, request=None
) -> ApiToken:
    record = _get_token_or_404(db, token_id)
    if record.revoked_at is not None:
        raise AppError(
            409, "TOKEN_REVOKED", "Token is already permanently revoked."
        )
    if record.disabled_at is None:
        record.disabled_at = utc_now()
        db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.api_token_disabled,
        actor_user_id=actor.id,
        target_type="api_token",
        target_id=str(record.id),
        metadata={"owner_user_id": record.owner_user_id},
        request=request,
    )
    return record


def reactivate_token(
    db: Session, *, actor: User, token_id: int, request=None
) -> ApiToken:
    record = _get_token_or_404(db, token_id)
    if record.revoked_at is not None:
        raise AppError(
            409,
            "TOKEN_REVOKED",
            "Permanently-revoked tokens cannot be reactivated.",
        )
    if record.disabled_at is not None:
        record.disabled_at = None
        db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.api_token_reactivated,
        actor_user_id=actor.id,
        target_type="api_token",
        target_id=str(record.id),
        metadata={"owner_user_id": record.owner_user_id},
        request=request,
    )
    return record


def admin_revoke_token(
    db: Session, *, actor: User, token_id: int, request=None
) -> ApiToken:
    record = _get_token_or_404(db, token_id)
    if record.revoked_at is None:
        record.revoked_at = utc_now()
    record.disabled_at = None
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.api_token_admin_revoked,
        actor_user_id=actor.id,
        target_type="api_token",
        target_id=str(record.id),
        metadata={"owner_user_id": record.owner_user_id},
        request=request,
    )
    return record


def admin_create_for(
    db: Session,
    *,
    actor: User,
    target_user: User,
    name: str,
    expires_at: datetime | None = None,
    scopes: str | None = None,
    request=None,
) -> tuple[ApiToken, str]:
    """Admin creates a token on behalf of `target_user`. Plaintext is
    returned to the admin once and forwarded out-of-band."""
    record, plaintext = create_token(
        db, owner=target_user, name=name, expires_at=expires_at, scopes=scopes
    )
    record_audit_event(
        db,
        event_type=AuditEventType.api_token_admin_created,
        actor_user_id=actor.id,
        target_type="api_token",
        target_id=str(record.id),
        metadata={
            "owner_user_id": target_user.id,
            "name": record.name,
            "scopes": record.scopes_list,
        },
        request=request,
    )
    return record, plaintext


def list_all_tokens(
    db: Session,
    *,
    q: str = "",
    owner_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ApiToken], int]:
    """Admin paginated query.

    `status` ∈ {`active`, `disabled`, `revoked`, `expired`}. `q` matches against
    owner display_name + email. Status precedence mirrors `_token_status`:
    revoked > expired > disabled > active.
    """
    now = utc_now()
    base = db.query(ApiToken).join(User, ApiToken.owner_user_id == User.id)
    if owner_id is not None:
        base = base.filter(ApiToken.owner_user_id == owner_id)
    if status == "active":
        base = base.filter(
            ApiToken.revoked_at.is_(None),
            ApiToken.disabled_at.is_(None),
            (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at >= now),
        )
    elif status == "expired":
        base = base.filter(
            ApiToken.revoked_at.is_(None),
            ApiToken.expires_at.is_not(None),
            ApiToken.expires_at < now,
        )
    elif status == "disabled":
        base = base.filter(
            ApiToken.revoked_at.is_(None),
            ApiToken.disabled_at.is_not(None),
            (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at >= now),
        )
    elif status == "revoked":
        base = base.filter(ApiToken.revoked_at.is_not(None))
    if q:
        like = f"%{q}%"
        base = base.filter(
            (User.display_name.ilike(like)) | (User.email.ilike(like))
        )

    total = base.count()
    rows = (
        base.order_by(ApiToken.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total
