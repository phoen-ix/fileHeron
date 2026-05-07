"""Manual CLI: promote a user (by email) to admin.

Usage (from the container):
    docker compose exec backend python -m scripts.promote_user user@example.com
"""
from __future__ import annotations

import sys

from app.database import SessionLocal
from app.models.audit_log import AuditEventType
from app.models.user import User, UserRole
from app.services.audit import record_audit_event
from app.utils.crypto import normalize_email


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m scripts.promote_user <email>", file=sys.stderr)
        return 2

    email = argv[1].strip().lower()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalize_email(email)).one_or_none()
        if user is None:
            print(f"no user found with email matching {email!r}", file=sys.stderr)
            return 1
        was = user.role
        cleaned = 0
        if user.role != UserRole.admin:
            from app.services import connection as connection_svc

            user.role = UserRole.admin
            cleaned = connection_svc.cleanup_connections_for_role_change(
                db, target=user, old_role=was
            )
        if not user.email_verified:
            user.email_verified = True
        metadata: dict[str, object] = {
            "from": was.value,
            "to": "admin",
            "reason": "manual_cli",
        }
        if cleaned:
            metadata["connections_pruned"] = cleaned
        record_audit_event(
            db,
            event_type=AuditEventType.role_changed,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata=metadata,
        )
        db.commit()
        print(f"promoted user id={user.id} email={user.email} (was {was.value})")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
