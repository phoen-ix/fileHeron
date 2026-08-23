"""Manual CLI: promote a user (by email) to admin.

Usage (from the container):
    docker compose exec backend python -m scripts.promote_user user@example.com
"""
from __future__ import annotations

# Run either way. The docs (README, CLAUDE.md) invoke these as
# `python scripts/<name>.py`, which puts scripts/ on sys.path but NOT the
# package root, so `import app` failed with ModuleNotFoundError - including for
# promote_user, the documented escape hatch when an admin loses their TOTP and
# recovery codes (audit 2026-07-30). `python -m scripts.<name>` always worked.
# Make the documented form work too rather than relying on the operator picking
# the right invocation while locked out.
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import sys  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.audit_log import AuditEventType  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services.audit import record_audit_event  # noqa: E402
from app.utils.crypto import normalize_email  # noqa: E402


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
            "via": "host-cli",
        }
        if cleaned:
            metadata["connections_pruned"] = cleaned
        record_audit_event(
            db,
            # actor_user_id=None, not the target's own id. There is no
            # self-service role change in this product, so `actor == target`
            # read as something that cannot happen through the API. NULL is what
            # every other operator-side write uses to mean "no signed-in actor";
            # `via: host-cli` in the metadata is what names the channel, matching
            # scripts/unblock_ip.py, which passes the same marker through
            # scan_guard.release.
            event_type=AuditEventType.role_changed,
            actor_user_id=None,
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
