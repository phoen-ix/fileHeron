"""Admin-side user CRUD: list/search/update/disable/force-password-reset.

Right-to-erasure lives in `services/erasure.py` because it does more
than mutate the row — it walks files + share recipients.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.refresh_token import RefreshToken
from ..models.user import User, UserRole
from .audit import record_audit_event

logger = logging.getLogger("fileheron.user_management")


def list_users(
    db: Session,
    *,
    q: str = "",
    role: UserRole | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[User], int]:
    """Return (rows, total). `q` matches display_name + email
    case-insensitively (substring)."""
    base = db.query(User)
    if role is not None:
        base = base.filter(User.role == role)
    if q:
        like = f"%{q.lower()}%"
        # MariaDB's LIKE is case-insensitive on utf8 collations by default;
        # SQLite needs explicit `LOWER()` for portability across tests.
        from sqlalchemy import func, or_
        base = base.filter(
            or_(
                func.lower(User.display_name).like(like),
                func.lower(User.email).like(like),
            )
        )
    total = base.count()
    rows = (
        base.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def update_user(
    db: Session,
    *,
    actor: User,
    target: User,
    display_name: Optional[str] = None,
    role: Optional[UserRole] = None,
    quota_bytes: Optional[int] = None,
    is_disabled: Optional[bool] = None,
    request=None,
) -> User:
    changed: dict[str, object] = {}
    if display_name is not None and display_name.strip() != target.display_name:
        target.display_name = display_name.strip()
        changed["display_name"] = display_name.strip()
    if role is not None and role != target.role:
        old_role = target.role
        target.role = role
        changed["role"] = {"from": old_role.value, "to": role.value}
        # Slot-aware: a client↔non-client flip invalidates connection
        # rows where `target` sat in the old slot (column names on
        # `ClientEmployeeConnection` lock each row to a slot, so the
        # rows literally describe the wrong relationship after the flip).
        # Drop them and let the helper repopulate shared_group rows for
        # the new slot.
        from . import connection as connection_svc

        cleaned = connection_svc.cleanup_connections_for_role_change(
            db, target=target, old_role=old_role
        )
        if cleaned:
            changed["connections_pruned"] = cleaned
        # 2FA-enforcement reflagging is no longer needed — the policy is
        # evaluated live (services.twofa_policy.is_2fa_required) so the
        # next request from `target` will redirect them through the
        # forced-setup flow without any column writes here.
    if quota_bytes is not None and quota_bytes != target.quota_bytes:
        target.quota_bytes = quota_bytes if quota_bytes > 0 else None
        changed["quota_bytes"] = target.quota_bytes
    if is_disabled is not None and is_disabled != target.is_disabled:
        target.is_disabled = is_disabled
        changed["is_disabled"] = is_disabled
        if is_disabled:
            # Disabled users: revoke every refresh token immediately so
            # session is killed across devices.
            db.query(RefreshToken).filter(
                RefreshToken.user_id == target.id,
                RefreshToken.revoked_at.is_(None),
            ).update(
                {RefreshToken.revoked_at: target.created_at.__class__.now()},
                synchronize_session=False,
            )

    if changed:
        db.flush()
        ev = (
            AuditEventType.user_disabled
            if changed.get("is_disabled") is True
            else AuditEventType.role_changed
            if "role" in changed
            else AuditEventType.user_registered  # closest existing for misc edits
        )
        record_audit_event(
            db,
            event_type=ev,
            actor_user_id=actor.id,
            target_type="user",
            target_id=target.id,
            metadata={"changes": changed},
            request=request,
        )
    return target


def force_password_reset(
    db: Session, *, actor: User, target: User, request=None
) -> str:
    """Generate a fresh password-reset token for `target`. Returns the
    plaintext (admin can share via out-of-band channel; in dev SMTP-less
    mode the email-with-logs-fallback also surfaces it).

    Caller commits."""
    from datetime import datetime, timedelta, timezone
    from ..models.password_reset_token import PasswordResetToken
    from ..utils.crypto import random_token, sha256_hex

    plaintext = random_token(32)
    record = PasswordResetToken(
        user_id=target.id,
        token_hash=sha256_hex(plaintext),
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
    )
    db.add(record)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.password_reset_requested,
        actor_user_id=actor.id,
        target_type="user",
        target_id=target.id,
        metadata={"forced_by_admin": True},
        request=request,
    )
    return plaintext


def get_or_404(db: Session, user_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).one_or_none()
    if u is None:
        raise AppError(404, "USER_NOT_FOUND", "User not found.")
    return u
