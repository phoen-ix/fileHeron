"""Idempotent admin bootstrap. Called by docker entrypoint on every start.

Reads ADMIN_BOOTSTRAP_EMAIL + ADMIN_BOOTSTRAP_PASSWORD from env. If the
matching user already exists, ensures role=admin + email_verified. If not,
creates them iff no admin exists yet.

Safe to run multiple times.
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
from app.services.admin_bootstrap import bootstrap_admin_if_configured  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        bootstrap_admin_if_configured(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
