"""JWT access tokens + refresh-token rotation, eviction, reuse detection.

Separated from `services/auth.py` so the login-flow surface (password +
2FA + email-verify + password-reset) doesn't drag in the session-token
machinery and vice versa. `services/auth.py` imports
`create_access_token`, `create_refresh_token`, and
`revoke_all_user_refresh_tokens` from here on the happy-path login;
routers/auth.py imports `rotate_refresh` and `logout` directly.

Refresh-token rotation invariants the audit relies on:
- Refresh row is hash-only in the DB; plaintext only exists in the
  cookie set by the router.
- Conditional UPDATE in `rotate_refresh` is the atomic guard against
  double-use (the second concurrent request sees rowcount=0 →
  treats as reuse → family-revoke).
- Per-user session cap is enforced via `enforce_session_cap` at every
  call site that mints a fresh refresh — oldest evicted first.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import jwt
from fastapi import Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.refresh_token import RefreshToken
from ..models.user import User
from ..utils.crypto import random_token, refresh_token_hash
from .audit import record_audit_event

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger("fileheron.jwt_session")


def _utcnow() -> datetime:
    # Naive UTC — matches what MariaDB returns for DATETIME columns.
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, settings, db: Session | None = None) -> tuple[str, int]:
    """Returns (token, expires_in_seconds).

    Uses AWARE UTC for timestamp math — naive .timestamp() is interpreted as
    local time and would emit incorrect Unix epochs.
    Adds a `jti` (random nonce) so two tokens issued in the same second
    are still distinguishable.

    `db` is optional: when supplied, the access-token TTL is read live from
    the admin-tunable settings registry (kv overlay, env default); without
    it the env value is used (keeps non-DB call sites working).
    """
    if db is not None:
        from . import settings_registry
        minutes = settings_registry.effective(db, settings_registry.K.ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now_aware = datetime.now(tz=timezone.utc)
    exp_aware = now_aware + timedelta(minutes=minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now_aware.timestamp()),
        "exp": int(exp_aware.timestamp()),
        "jti": uuid.uuid4().hex,
        "type": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, minutes * 60


def resolve_user_from_access_token(db: Session, token: str, settings: "Settings") -> User:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "Access token has expired.") from None
    except jwt.InvalidTokenError:
        raise AppError(401, "INVALID_TOKEN", "Invalid access token.") from None

    if payload.get("type") != "access":
        raise AppError(401, "INVALID_TOKEN", "Wrong token type.")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise AppError(401, "INVALID_TOKEN", "Invalid token claims.") from None

    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(401, "AUTH_REQUIRED", "Authentication failed.")
    return user


# ---------------------------------------------------------------------------
# Refresh tokens (DB-backed, rotated, reuse-detected)
# ---------------------------------------------------------------------------


def enforce_session_cap(
    db: Session, *, user_id: int, cap: int, request: Request | None
) -> int:
    """Revoke the oldest excess active tokens so that creating one
    more leaves the user at exactly `cap`. Returns number revoked.
    Called once per `create_refresh_token` from any auth flow."""
    if cap <= 0:
        return 0
    now = _utcnow()
    active_q = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    active_count = active_q.count()
    if active_count < cap:
        return 0
    # Need to evict (active_count - cap + 1) to make room for the new one.
    excess = active_count - cap + 1
    oldest = (
        active_q.order_by(RefreshToken.created_at.asc()).limit(excess).all()
    )
    for token in oldest:
        token.revoked_at = now
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_evicted,
            actor_user_id=user_id,
            target_type="refresh_token",
            target_id=str(token.id),
            metadata={
                "evicted_token_id": token.id,
                "reason": "session_cap",
                "cap": cap,
            },
            request=request,
        )
    db.flush()
    return len(oldest)


def create_refresh_token(db: Session, user: User, request: Request | None, settings) -> tuple[RefreshToken, str]:
    # Cap-enforcement chokepoint — every auth flow (password, recovery,
    # OIDC, WebAuthn, register-from-invite) ends here, so this gate
    # covers them all. The eviction is non-security-relevant
    # (`refresh_token_evicted` audit) — distinct from
    # `refresh_token_reused` family-revoke for compromised chains.
    from . import settings_registry
    enforce_session_cap(
        db,
        user_id=user.id,
        cap=settings_registry.effective(db, settings_registry.K.MAX_ACTIVE_SESSIONS_PER_USER),
        request=request,
    )

    plaintext = random_token(48)  # 64 raw bytes → 86-char b64url
    now = _utcnow()
    refresh_days = settings_registry.effective(
        db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
    )
    record = RefreshToken(
        user_id=user.id,
        token_hash=refresh_token_hash(plaintext),
        expires_at=now + timedelta(days=refresh_days),
        created_ip=(request.client.host if request and request.client else None),
        created_ua=(request.headers.get("user-agent", "")[:255] if request else None),
    )
    db.add(record)
    db.flush()
    return record, plaintext


def revoke_all_user_refresh_tokens(db: Session, user_id: int) -> int:
    """Coarse-but-safe family revoke. Used when reuse is detected or on
    password reset / change. Returns number of rows affected."""
    now = _utcnow()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result.rowcount or 0


def rotate_refresh(
    db: Session,
    *,
    refresh_token_plain: str,
    request: Request | None,
    settings,
) -> tuple[User, str, int, str]:
    """Validate + rotate the refresh token. On reuse → revoke all of the
    user's refresh tokens and raise TOKEN_REUSE.

    Returns (user, new_access_token, expires_in_seconds, new_refresh_token_plain).
    """
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == refresh_token_hash(refresh_token_plain))
        .one_or_none()
    )
    if record is None:
        raise AppError(401, "INVALID_REFRESH", "Invalid refresh token.")
    if record.expires_at < _utcnow():
        raise AppError(401, "INVALID_REFRESH", "Refresh token expired.")
    if record.revoked_at is not None:
        # Reuse detected → kill all sessions for this user, audit, raise.
        revoke_all_user_refresh_tokens(db, record.user_id)
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_reused,
            actor_user_id=record.user_id,
            target_type="refresh_token",
            target_id=record.id,
            request=request,
        )
        db.commit()
        raise AppError(401, "TOKEN_REUSE", "Refresh token reuse detected; all sessions revoked.")

    # Conditional UPDATE → atomic check-and-revoke. If two requests race, the
    # second sees affected_rows=0 and we treat it as reuse.
    now = _utcnow()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if (result.rowcount or 0) == 0:
        revoke_all_user_refresh_tokens(db, record.user_id)
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_reused,
            actor_user_id=record.user_id,
            target_type="refresh_token",
            target_id=record.id,
            metadata={"reason": "race"},
            request=request,
        )
        db.commit()
        raise AppError(401, "TOKEN_REUSE", "Refresh token reuse detected; all sessions revoked.")

    user = db.query(User).filter(User.id == record.user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(403, "ACCOUNT_DISABLED", "Account unavailable.")

    new_record, new_plain = create_refresh_token(db, user, request, settings)
    record.replaced_by_id = new_record.id
    db.flush()

    access, expires_in = create_access_token(user.id, settings, db)
    record_audit_event(
        db,
        event_type=AuditEventType.refresh_token_rotated,
        actor_user_id=user.id,
        target_type="refresh_token",
        target_id=new_record.id,
        request=request,
    )
    return user, access, expires_in, new_plain


def logout(db: Session, *, refresh_token_plain: str, request: Request | None) -> None:
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == refresh_token_hash(refresh_token_plain))
        .one_or_none()
    )
    if record is None or record.revoked_at is not None:
        return  # idempotent
    record.revoked_at = _utcnow()
    record_audit_event(
        db,
        event_type=AuditEventType.logout,
        actor_user_id=record.user_id,
        target_type="refresh_token",
        target_id=record.id,
        request=request,
    )
