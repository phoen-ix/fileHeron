"""Admin bootstrap. Idempotent on every backend startup.

Two paths:

1. ADMIN_BOOTSTRAP_EMAIL set + ADMIN_BOOTSTRAP_PASSWORD set + no admin exists →
   create that user as admin (email-verified) on startup.
2. ADMIN_BOOTSTRAP_EMAIL set + the user exists + **setup is not yet complete** →
   ensure they're role=admin and email_verified=true (first-run promotion).

If ADMIN_BOOTSTRAP_EMAIL is empty, neither path runs.

Both paths are bounded to first run. Path 2 used to promote on EVERY boot, which
made a deliberate demotion or disablement of that one account self-reverting -
see the comment on the guard for why the sticky `setup.completed_at` flag is the
right authority for both.
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

    # Path 2: FIRST-RUN promote-and-verify, not a standing re-promotion.
    #
    # This ran unconditionally on every boot and force-set role=admin,
    # email_verified and is_disabled=False. So an administrator deliberately
    # demoted or disabled after an incident silently regained everything on the
    # next restart - and with `restart: unless-stopped` plus the in-app
    # self-update, restarts are routine. The env var is operator-controlled, so
    # this is IR-persistence rather than a remotely reachable hole, but it
    # quietly reverted the product's own admin-revocation control.
    #
    # `is_setup_complete` is the sticky one-way flag the 2026-07-30 audit added
    # for exactly this class of problem; reuse it rather than inventing a second
    # "this instance is past bootstrap" authority. Once setup is done, the
    # documented recovery is `scripts/promote_user.py`, which needs shell access
    # to the host - the right bar for reinstating an admin.
    from .setup import is_setup_complete

    if is_setup_complete(db):
        logger.info(
            "admin_bootstrap: setup already complete; leaving %s as-is "
            "(use scripts/promote_user.py to reinstate an admin)",
            user.email,
        )
        return

    changed = False
    metadata: dict[str, object] = {"reason": "idempotent_promote"}
    if user.role != UserRole.admin:
        from . import connection as connection_svc

        old_role = user.role
        user.role = UserRole.admin
        cleaned = connection_svc.cleanup_connections_for_role_change(
            db, target=user, old_role=old_role
        )
        if cleaned:
            metadata["connections_pruned"] = cleaned
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
            metadata=metadata,
        )
        db.commit()
        logger.info("admin_bootstrap: promoted/verified existing user %s", user.email)
