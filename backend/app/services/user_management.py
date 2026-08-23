"""Admin-side user CRUD: list/search/update/disable/force-password-reset.

Right-to-erasure lives in `services/erasure.py` because it does more
than mutate the row - it walks files + share recipients.
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.refresh_token import RefreshToken
from ..models.user import User, UserRole
from ..utils.timeutil import utc_now
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
        base.order_by(User.created_at.desc(), User.id.desc())
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
    display_name: str | None = None,
    role: UserRole | None = None,
    quota_bytes: int | None = None,
    is_disabled: bool | None = None,
    request=None,
) -> User:
    changed: dict[str, object] = {}

    # Last-admin / self-demotion guard (audit M17): refuse a change that would
    # leave the organization with zero enabled admins - either demoting an
    # enabled admin off the admin role or disabling an enabled admin. Without
    # this, the sole admin can lock the whole org out of every /admin route
    # (role is resolved live, so the demotion takes effect immediately and the
    # user-update endpoint itself becomes 403). Recovery would then require the
    # out-of-band scripts/promote_user.py escape hatch.
    removing_admin_privilege = (
        target.role == UserRole.admin
        and not target.is_disabled
        and (
            (role is not None and role != UserRole.admin)
            or is_disabled is True
        )
    )
    if removing_admin_privilege:
        other_admins = (
            db.query(User)
            .filter(
                User.role == UserRole.admin,
                User.is_disabled.is_(False),
                User.id != target.id,
            )
            .count()
        )
        if other_admins == 0:
            raise AppError(
                400,
                "LAST_ADMIN",
                "Cannot remove the last remaining admin. Promote another admin first.",
            )
        # The count above is a read; the mutation happens below in the same
        # transaction with nothing serialising them. Two admins demoting EACH
        # OTHER concurrently both see one other admin and both proceed, leaving
        # zero. Locking the target row does not help - they are different rows -
        # so the guarantee has to be a re-check AFTER the change is applied,
        # which is at the end of this function (audit 2026-07-30).
        _reassert_admin_remains = True
    else:
        _reassert_admin_remains = False

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
        # 2FA-enforcement reflagging is no longer needed - the policy is
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
                {RefreshToken.revoked_at: utc_now()},
                synchronize_session=False,
            )

    if _reassert_admin_remains:
        # Re-check with the change APPLIED. db.flush() makes it visible to this
        # transaction, and on MariaDB the FOR UPDATE serialises concurrent
        # demotions so the loser sees the winner's result and aborts.
        db.flush()
        still_admin = (
            db.query(User)
            .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
            .with_for_update()
            .first()
        )
        if still_admin is None:
            raise AppError(
                400,
                "LAST_ADMIN",
                "Cannot remove the last remaining admin. Promote another admin first.",
            )

    if changed:
        db.flush()
        # One row per semantic change, not one row with a guessed type.
        #
        # The old expression picked a SINGLE event by precedence, so a PATCH
        # that both demoted someone and disabled them emitted only
        # `user_disabled` - the privilege change was invisible to anyone
        # filtering the audit log for `role_changed`, which is exactly the query
        # a reviewer runs. Re-enabling an account and editing a quota both
        # landed as `user_registered`, a lie about what happened, chosen because
        # it was "the closest existing" member (audit 2026-07-30).
        events: list[AuditEventType] = []
        if "role" in changed:
            events.append(AuditEventType.role_changed)
        if changed.get("is_disabled") is True:
            events.append(AuditEventType.user_disabled)
        elif changed.get("is_disabled") is False:
            events.append(AuditEventType.user_enabled)
        if not events or any(k not in ("role", "is_disabled") for k in changed):
            events.append(AuditEventType.user_updated)
        for ev in events:
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


async def create_user_as_admin(
    db: Session,
    *,
    actor: User,
    email: str,
    display_name: str,
    password: str,
    target_role: UserRole,
    initial_group_ids: list[int] | None = None,
    request=None,
) -> User:
    """Create a user account immediately - no invite, email pre-verified,
    with an admin-set password the user can sign in with right away.

    Mirrors the invite flow's invariants (uniqueness / pending-invite /
    group-existence guards) and reuses its group + connection helpers so a
    directly-created user is indistinguishable from an invited one. Caller
    commits."""
    from ..utils.crypto import argon2_hash, normalize_email
    from . import invite as invite_svc
    from .hibp import assert_password_not_breached

    em = normalize_email(email)
    if not em:
        raise AppError(400, "INVALID_EMAIL", "Email cannot be empty.")
    if not display_name or not display_name.strip():
        raise AppError(400, "INVALID_DISPLAY_NAME", "Display name is required.")
    await assert_password_not_breached(db, password)

    if db.query(User).filter(User.email == em).one_or_none() is not None:
        raise AppError(409, "USER_EXISTS", "An account already exists for this email.")
    if invite_svc.has_pending_invite(db, email_value=em):
        raise AppError(
            409,
            "INVITE_PENDING",
            "A pending invite exists for this email - revoke it first, or activate it.",
        )

    group_ids = list(initial_group_ids or [])
    groups = []
    if group_ids:
        from ..models.group import Group

        found = db.query(Group).filter(Group.id.in_(group_ids)).all()
        missing = sorted(set(group_ids) - {g.id for g in found})
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more groups no longer exist.",
                details={"missing_group_ids": missing},
            )
        groups = found

    user = User(
        email=em,
        password_hash=argon2_hash(password),
        display_name=display_name.strip()[:120],
        role=target_role,
        locale=actor.locale,
        email_verified=True,
        is_disabled=False,
        created_by_id=actor.id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # A parallel registration won the UNIQUE(email) race between the
        # pre-flight SELECT above and this flush. The invite path already
        # answers 409 here; leaving this one bare surfaced a raw 500 to the
        # user plus an error-log row and an admin alert email, for a routine
        # collision (audit 2026-07-30).
        raise AppError(
            409, "USER_EXISTS", "An account already exists for this email."
        ) from None

    from .group import add_member as _add_group_member

    for grp in groups:
        _add_group_member(db, actor=actor, group=grp, user=user, request=request)

    # Sticky client↔employee connection, same as the invite path.
    from .connection import record_invite_connection

    record_invite_connection(db, inviter=actor, invitee=user)

    record_audit_event(
        db,
        event_type=AuditEventType.user_created_by_admin,
        actor_user_id=actor.id,
        target_type="user",
        target_id=user.id,
        metadata={"role": target_role.value, "group_ids": group_ids},
        request=request,
    )
    logger.info("admin %d created user %s (id=%d) directly", actor.id, user.email, user.id)
    return user
