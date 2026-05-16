"""Programmatic API tokens.

Wire format: ``fh_<8-hex-prefix>_<43-char-base64url-secret>``.
- prefix is 4 bytes of crypto-random rendered as 8 hex chars (indexed unique
  in DB; allows lookup without iterating).
- secret is 32 bytes of crypto-random urlsafe-base64 (rstrip "=" → 43 chars).

Backend stores: prefix, last4 (display only), secret_hash (SHA-256 of the
secret half — high-entropy random, no Argon2 needed).

Verify:
    parse "fh_<prefix>_<secret>"
    SELECT row by prefix
    constant-time compare sha256(secret) to row.secret_hash
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.api_token import ApiToken
from ..models.audit_log import AuditEventType
from ..models.group_member import GroupMember
from ..models.user import User, UserRole
from ..utils.crypto import constant_time_equals, sha256_hex
from .audit import record_audit_event

TOKEN_PREFIX_BYTES = 4  # 8 hex chars
TOKEN_SECRET_BYTES = 32  # 43 b64url chars (no padding)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _gen_prefix() -> str:
    return secrets.token_hex(TOKEN_PREFIX_BYTES)


def _gen_secret() -> str:
    raw = secrets.token_bytes(TOKEN_SECRET_BYTES)
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_token(db: Session, *, owner: User, name: str) -> tuple[ApiToken, str]:
    """Returns (record, plaintext_token). The plaintext is shown to the user
    once; only the SHA-256 hash is stored."""
    prefix = _gen_prefix()
    secret = _gen_secret()
    plaintext = f"fh_{prefix}_{secret}"

    record = ApiToken(
        owner_user_id=owner.id,
        name=name.strip()[:120] or "(unnamed)",
        prefix=prefix,
        last4=secret[-4:],
        secret_hash=sha256_hex(secret),
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
    if record.disabled_at is not None:
        # Distinct code so admin/UX can tell "temporarily off" from "gone".
        raise AppError(401, "API_TOKEN_DISABLED", "API token is disabled.")
    if not constant_time_equals(record.secret_hash, sha256_hex(secret)):
        raise AppError(401, "INVALID_API_TOKEN", "Invalid API token.")

    record.last_used_at = _utcnow()
    db.flush()
    return record


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
        record.revoked_at = _utcnow()
    # Once revoked, disabled is moot — clear it for cleanliness.
    record.disabled_at = None
    db.flush()


# ---------------------------------------------------------------------------
# Policy gate (post-Phase 10)
# ---------------------------------------------------------------------------

POLICY_MODES = ("everyone", "employees_admins", "admins_only", "disabled")
DEFAULT_POLICY_MODE = "everyone"


def _resolve_policy(db: Session) -> tuple[str, list[int], list[int]]:
    """Read policy from app_settings. Returns (mode, allowed_user_ids,
    allowed_group_ids). Falls back to defaults for an unconfigured deploy
    so existing CI scripts keep working until an admin sets a stricter
    policy."""
    from . import settings as settings_svc

    mode = settings_svc.get(db, settings_svc.Keys.API_TOKEN_POLICY_MODE) or DEFAULT_POLICY_MODE
    if mode not in POLICY_MODES:
        mode = DEFAULT_POLICY_MODE

    raw_users = settings_svc.get(db, settings_svc.Keys.API_TOKEN_ALLOWED_USERS) or "[]"
    raw_groups = settings_svc.get(db, settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS) or "[]"
    import json

    try:
        user_ids = [int(x) for x in json.loads(raw_users)]
    except (ValueError, TypeError):
        user_ids = []
    try:
        group_ids = [int(x) for x in json.loads(raw_groups)]
    except (ValueError, TypeError):
        group_ids = []
    return mode, user_ids, group_ids


def is_allowed_to_create(db: Session, user: User) -> bool:
    """Returns True if the user can mint an API token under the current
    policy. Admin always passes (operator escape hatch)."""
    if user.role == UserRole.admin:
        return True
    mode, allowed_users, allowed_groups = _resolve_policy(db)
    if mode == "everyone":
        return True
    if mode == "employees_admins" and user.role == UserRole.employee:
        return True
    # Allowlist additive on top of the base mode.
    if user.id in allowed_users:
        return True
    if allowed_groups:
        hit = (
            db.query(GroupMember.user_id)
            .filter(
                GroupMember.user_id == user.id,
                GroupMember.group_id.in_(allowed_groups),
            )
            .first()
        )
        if hit is not None:
            return True
    return False


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
        record.disabled_at = _utcnow()
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
        record.revoked_at = _utcnow()
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
    db: Session, *, actor: User, target_user: User, name: str, request=None
) -> tuple[ApiToken, str]:
    """Admin creates a token on behalf of `target_user`. Plaintext is
    returned to the admin once and forwarded out-of-band."""
    record, plaintext = create_token(db, owner=target_user, name=name)
    record_audit_event(
        db,
        event_type=AuditEventType.api_token_admin_created,
        actor_user_id=actor.id,
        target_type="api_token",
        target_id=str(record.id),
        metadata={"owner_user_id": target_user.id, "name": record.name},
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

    `status` ∈ {`active`, `disabled`, `revoked`}. `q` matches against
    owner display_name + email.
    """
    base = db.query(ApiToken).join(User, ApiToken.owner_user_id == User.id)
    if owner_id is not None:
        base = base.filter(ApiToken.owner_user_id == owner_id)
    if status == "active":
        base = base.filter(
            ApiToken.revoked_at.is_(None), ApiToken.disabled_at.is_(None)
        )
    elif status == "disabled":
        base = base.filter(
            ApiToken.revoked_at.is_(None), ApiToken.disabled_at.is_not(None)
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
