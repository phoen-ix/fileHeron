"""First-admin setup wizard (v1.0.0).

Replaces the env-var `ADMIN_BOOTSTRAP_*` bootstrap path with an
anonymous-accessible web wizard at `/setup`. The wizard is open until
the first admin exists, then locks itself.

The env-driven path (`services/admin_bootstrap.py`) is retained as a
fallback for CI / scripting. The wizard runs in addition; whichever
fires first wins (idempotent due to email-uniqueness and the
is_setup_complete check inside the transaction).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import Locale, User, UserRole
from ..utils.crypto import argon2_hash, normalize_email
from ..utils.timeutil import utc_now
from . import settings as settings_svc
from .audit import record_audit_event
from .hibp import assert_password_not_breached

logger = logging.getLogger("fileheron.setup")


def is_setup_complete(db: Session) -> bool:
    """True once setup has been completed - STICKY, and independent of whether
    an admin currently exists.

    This used to be defined purely as "at least one non-disabled admin exists",
    which made it reversible: any path to zero enabled admins re-opened
    POST /api/setup/admin, and that route is anonymous and mounted ungated
    (main.py). Anyone on the internet could then create themselves an admin on a
    live instance with real data. `update_user`'s last-admin guard is the first
    line of defence, but defining the wizard's availability as the *absence* of
    admins makes any future hole in that guard - or a botched restore, or a
    manual DB edit - an immediate remote-takeover (audit 2026-07-30).
    A one-way flag removes the consequence rather than only narrowing the race.

    The documented recovery for a genuinely lost admin is the CLI escape hatch
    (`docker compose exec backend python scripts/promote_user.py <email>`), which
    requires shell access to the host - the right bar for that operation.

    The admin-exists check is kept as the fallback so instances that completed
    setup before this flag existed are still recognised as complete.
    """
    if settings_svc.get(db, settings_svc.Keys.SETUP_COMPLETED_AT):
        return True
    return (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .first()
        is not None
    )


async def complete_setup(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
) -> User:
    """Create the first admin via the web wizard. Race-safe via the
    is_setup_complete check + the unique email constraint - second
    submission gets 409.

    Caller commits."""
    if is_setup_complete(db):
        raise AppError(409, "SETUP_ALREADY_COMPLETE", "An admin account already exists.")

    em = normalize_email(email)
    if not em:
        raise AppError(400, "INVALID_EMAIL", "Email cannot be empty.")
    if not password or len(password) < 12:
        raise AppError(400, "WEAK_PASSWORD", "Password must be at least 12 characters.")
    if not display_name or not display_name.strip():
        raise AppError(400, "INVALID_DISPLAY_NAME", "Display name is required.")
    # Belt-and-braces: refuse if a user (any role) with this email already
    # exists - the unique constraint would catch it, but we want a clean
    # AppError envelope rather than IntegrityError → 500.
    #
    # BEFORE the HIBP call, not after. `assert_password_not_breached` makes an
    # outbound HTTPS request to api.pwnedpasswords.com, and this route is
    # anonymous, so ordering it first let a caller drive that request with an
    # email that was going to be rejected anyway. Cheap local checks first is
    # also just the right order.
    existing = db.query(User).filter(User.email == em).one_or_none()
    if existing is not None:
        raise AppError(409, "EMAIL_TAKEN", "A user with this email already exists.")

    await assert_password_not_breached(db, password)

    user = User(
        email=em,
        password_hash=argon2_hash(password),
        display_name=display_name.strip()[:120],
        role=UserRole.admin,
        locale=Locale.en,
        email_verified=True,
        is_disabled=False,
    )
    db.add(user)
    db.flush()

    # One-way: from here on the wizard stays closed even if every admin is
    # later disabled or deleted.
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SETUP_COMPLETED_AT,
        value=utc_now().isoformat(),
        actor=None,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.admin_bootstrapped,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        metadata={"reason": "setup_wizard"},
    )
    logger.warning("setup wizard created admin %s (id=%d)", user.email, user.id)
    return user
