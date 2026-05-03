"""Seed a dev test account on startup. Idempotent. Refuses to run when
ENVIRONMENT=production.

If TEST_ACCOUNT_EMAIL == ADMIN_BOOTSTRAP_EMAIL, the seeded account is admin
(after `create_admin.py` runs at boot, which it does). Otherwise the seeded
account is a verified `client`.
"""
from __future__ import annotations

import sys

from app.config import settings
from app.database import SessionLocal
from app.models.audit_log import AuditEventType
from app.models.user import Locale, User, UserRole
from app.services.audit import record_audit_event
from app.utils.crypto import argon2_hash, normalize_email


def main() -> int:
    if settings.is_production:
        print("[seed_dev] refusing to run in production environment", file=sys.stderr)
        return 0

    email = settings.TEST_ACCOUNT_EMAIL.strip()
    password = settings.TEST_ACCOUNT_PASSWORD.strip()
    display_name = settings.TEST_ACCOUNT_DISPLAY_NAME.strip()
    if not (email and password and display_name):
        return 0

    db = SessionLocal()
    try:
        normalized = normalize_email(email)
        existing = db.query(User).filter(User.email == normalized).one_or_none()
        if existing is not None:
            return 0  # idempotent

        user = User(
            email=normalized,
            password_hash=argon2_hash(password),
            display_name=display_name,
            role=UserRole.client,
            locale=Locale.en,
            email_verified=True,
            is_disabled=False,
        )
        db.add(user)
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.user_registered,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "dev_seed"},
        )
        db.commit()
        print(f"[seed_dev] seeded test user id={user.id} email={user.email}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
