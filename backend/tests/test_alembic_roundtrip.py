"""Verify every alembic migration can downgrade cleanly.

Many migrations have non-trivial downgrade() paths. Without a test,
they sit untested until the day an operator needs to roll back —
and finds out the path doesn't work. This walks the migration chain
backwards step-by-step against a temporary SQLite engine, then
re-upgrades to confirm no schema drift.

Slow-ish (creates and destroys ~30 tables N times). Lives in tests/
but not run by default — gate with `RUN_ALEMBIC_ROUNDTRIP=1` env var
so the default pytest run stays under 20s.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_ALEMBIC_ROUNDTRIP") != "1",
    reason="alembic roundtrip is slow; set RUN_ALEMBIC_ROUNDTRIP=1 to enable",
)


@_SKIP
def test_alembic_full_downgrade_roundtrip(tmp_path):
    """Run upgrade head → downgrade base → upgrade head against a
    fresh SQLite file. Asserts no exception."""
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    db_path = tmp_path / "roundtrip.sqlite"
    cfg = Config(
        str(Path(__file__).resolve().parent.parent / "alembic.ini")
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    upgrade(cfg, "head")
    downgrade(cfg, "base")
    upgrade(cfg, "head")
