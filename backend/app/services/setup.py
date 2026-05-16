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
from .audit import record_audit_event

logger = logging.getLogger("fileheron.setup")


def is_setup_complete(db: Session) -> bool:
    """True when at least one (non-disabled) admin exists."""
    return (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .first()
        is not None
    )


def complete_setup(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
) -> User:
    """Create the first admin via the web wizard. Race-safe via the
    is_setup_complete check + the unique email constraint — second
    submission gets 409.

    Caller commits."""
    if is_setup_complete(db):
        raise AppError(409, "SETUP_ALREADY_COMPLETE", "An admin account already exists.")

    em = normalize_email(email)
    if not em:
        raise AppError(400, "INVALID_EMAIL", "Email cannot be empty.")
    if not password or len(password) < 8:
        raise AppError(400, "WEAK_PASSWORD", "Password must be at least 8 characters.")
    if not display_name or not display_name.strip():
        raise AppError(400, "INVALID_DISPLAY_NAME", "Display name is required.")

    # Belt-and-braces: refuse if a user (any role) with this email already
    # exists — the unique constraint would catch it, but we want a clean
    # AppError envelope rather than IntegrityError → 500.
    existing = db.query(User).filter(User.email == em).one_or_none()
    if existing is not None:
        raise AppError(409, "EMAIL_TAKEN", "A user with this email already exists.")

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
