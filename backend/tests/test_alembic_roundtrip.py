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


# Columns/tables that legitimately differ between the models and the migrated
# schema. Keep this list short and justified - every entry is a hole in the
# check.
_IGNORED_TABLES: set[str] = set()

# The comparison is scoped to tables and columns on purpose. Index and
# constraint diffs are dominated by naming noise - a migration that names an
# index explicitly vs a model that declares `index=True` and gets alembic's
# `ix_<table>_<col>`, and MariaDB reflecting UNIQUE constraints back as
# indexes. Neither can break an application at runtime, and gating on them
# would mean rewriting a dozen historical migrations to buy nothing. Missing or
# extra COLUMNS are the failure this exists to catch: the attribute the code
# reads is simply not there in production.
_STRUCTURAL_OPS = frozenset({"add_table", "remove_table", "add_column", "remove_column"})


@_SKIP
def test_models_match_the_migrated_schema():
    """The models and the migration chain must describe the same database.

    conftest builds the test schema with ``Base.metadata.create_all`` on SQLite;
    production runs the alembic chain on MariaDB. Nothing compared the two, so a
    column added to a model but forgotten in a migration was green in every test
    and a 500 in production the moment that attribute was read. That class has
    already bitten this repo once (v1.51.0, the reserved word ``key``); this is
    the check that would have caught it (audit 2026-07-30).

    Runs in the alembic-roundtrip job, on the real engine, immediately after the
    chain has been re-applied - so it compares against exactly the schema an
    operator gets."""
    from alembic.autogenerate import compare_metadata
    from alembic.command import upgrade
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    import app.models  # noqa: F401  - registers every model on Base.metadata
    from app.config import settings
    from app.database import Base

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    upgrade(cfg, "head")

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": False,  # MariaDB type reflection is noisy
                    "include_name": lambda name, type_, _parent: not (
                        type_ == "table" and name in _IGNORED_TABLES | {"alembic_version"}
                    ),
                },
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()

    structural = [e for e in _flatten(diff) if e and e[0] in _STRUCTURAL_OPS]
    assert not structural, (
        "the models and the migrated schema disagree on tables/columns - a "
        "model change is missing a migration (or vice versa):\n"
        + "\n".join(f"  {_describe(e)}" for e in structural)
    )


def test_the_comparison_would_actually_catch_a_missing_column():
    """Negative control, and NOT skipped - it runs on SQLite in the normal
    suite.

    The check above only ever runs in one CI job against a live MariaDB, so if
    the filtering were wrong it would pass green forever and nobody would
    notice. This builds the exact drift it exists to catch - a column present
    in the models and absent from the database - and proves the filter reports
    it."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

    engine = create_engine("sqlite:///:memory:")
    db_md = MetaData()
    Table("widget", db_md, Column("id", Integer, primary_key=True))
    db_md.create_all(engine)

    model_md = MetaData()
    Table(
        "widget", model_md,
        Column("id", Integer, primary_key=True),
        Column("colour", String(16)),  # the column somebody forgot to migrate
    )

    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": False})
            diff = compare_metadata(ctx, model_md)
    finally:
        engine.dispose()

    structural = [e for e in _flatten(diff) if e and e[0] in _STRUCTURAL_OPS]
    assert [e[0] for e in structural] == ["add_column"], structural
    assert "widget.colour" in _describe(structural[0])


def _flatten(diff):
    for entry in diff:
        if isinstance(entry, list):  # alembic groups per-table modify_* diffs
            yield from entry
        else:
            yield entry


def _describe(entry) -> str:
    op = entry[0]
    if op in ("add_table", "remove_table"):
        return f"{op}: {entry[1].name}"
    if op in ("add_column", "remove_column"):
        _schema, table, col = entry[1], entry[2], entry[3]
        return f"{op}: {table}.{col.name} ({col.type})"
    return str(entry)
