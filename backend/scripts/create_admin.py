"""Idempotent admin bootstrap. Called by docker entrypoint on every start.

Reads ADMIN_BOOTSTRAP_EMAIL + ADMIN_BOOTSTRAP_PASSWORD from env. If the
matching user already exists, ensures role=admin + email_verified. If not,
creates them iff no admin exists yet.

Safe to run multiple times.
"""
from __future__ import annotations

import sys

from app.database import SessionLocal
from app.services.admin_bootstrap import bootstrap_admin_if_configured


def main() -> int:
    db = SessionLocal()
    try:
        bootstrap_admin_if_configured(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
