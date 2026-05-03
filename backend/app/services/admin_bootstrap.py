"""Admin bootstrap. Idempotent on every backend startup.

Two paths:

1. ADMIN_BOOTSTRAP_EMAIL set + ADMIN_BOOTSTRAP_PASSWORD set + no admin exists →
   create that user as admin (email-verified) on startup.
2. ADMIN_BOOTSTRAP_EMAIL set + the user exists → ensure they're role=admin and
   email_verified=true on startup (idempotent promotion). This is the same
   pattern as reclaim's `promoteByEmail`.

If ADMIN_BOOTSTRAP_EMAIL is empty, neither path runs.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models.audit_log import AuditEventType
from ..models.user import Locale, User, UserRole
from ..utils.crypto import argon2_hash, normalize_email
from .audit import record_audit_event

logger = logging.getLogger("fileheron.bootstrap")


def bootstrap_admin_if_configured(db: Session) -> None:
    email = settings.ADMIN_BOOTSTRAP_EMAIL.strip()
    if not email:
        return

    em_hash = normalize_email(email)
    user = db.query(User).filter(User.email == em_hash).one_or_none()

    if user is None:
        # Path 1: create iff password also set and no admin exists yet.
        password = settings.ADMIN_BOOTSTRAP_PASSWORD.strip()
        if not password:
            logger.info("admin_bootstrap: no user with that email yet, no password set; skipping create")
            return

        any_admin = db.query(User).filter(User.role == UserRole.admin).first()
        if any_admin is not None:
            logger.info("admin_bootstrap: an admin already exists; refusing to auto-create another")
            return

        user = User(
            email=normalize_email(email),
            password_hash=argon2_hash(password),
            display_name="admin",
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
            metadata={"reason": "auto_create"},
        )
        db.commit()
        logger.warning("admin_bootstrap: created admin user %s", user.email)
        return

    # Path 2: idempotent promote-and-verify.
    changed = False
    if user.role != UserRole.admin:
        user.role = UserRole.admin
        changed = True
    if not user.email_verified:
        user.email_verified = True
        changed = True
    if user.is_disabled:
        user.is_disabled = False
        changed = True
    if changed:
        record_audit_event(
            db,
            event_type=AuditEventType.admin_bootstrapped,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "idempotent_promote"},
        )
        db.commit()
        logger.info("admin_bootstrap: promoted/verified existing user %s", user.email)
