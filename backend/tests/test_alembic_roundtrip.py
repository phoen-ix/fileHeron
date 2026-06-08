"""Verify every alembic migration downgrades cleanly on the real engine.

Walks the whole chain up -> down -> up against the *configured* database
(``settings.database_url``, which ``alembic/env.py`` binds). In CI this runs in a
dedicated ``alembic-roundtrip`` job against a disposable MariaDB service (sets
``RUN_ALEMBIC_ROUNDTRIP=1`` + ``DB_*``); locally, point ``DB_*`` at a throwaway
MariaDB and set the flag.

Skipped by default: it needs a real MySQL/MariaDB (the migrations use MySQL DDL
the SQLite test harness can't run) and rewrites a whole schema. This is the only
thing that exercises the downgrade() paths - which otherwise sit untested until
an operator (or a rollback) needs them.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_ALEMBIC_ROUNDTRIP") != "1",
    reason="needs a real MariaDB; set RUN_ALEMBIC_ROUNDTRIP=1 + DB_* to enable",
)


@_SKIP
def test_alembic_full_downgrade_roundtrip():
    """upgrade head -> downgrade base -> upgrade head against the configured
    database. Asserts no exception - i.e. every migration's downgrade() works on
    the engine production actually uses."""
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    # env.py binds the URL from settings.database_url (the DB_* env). No need to
    # set sqlalchemy.url here - and a SQLite override would be ignored anyway.
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))

    upgrade(cfg, "head")
    downgrade(cfg, "base")
    upgrade(cfg, "head")
