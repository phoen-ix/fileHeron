"""Verify every alembic migration downgrades cleanly on the real engine.

Walks the whole chain up -> down -> up against the *configured* database
(``settings.database_url``, which ``alembic/env.py`` binds). In CI this runs in a
dedicated ``alembic-roundtrip`` job against a disposable MariaDB service (sets
``RUN_ALEMBIC_ROUNDTRIP=1`` + ``DB_*``); locally, run ``make test-mariadb``,
which stands one up in a throwaway container and removes it again. Do not
hand-roll that container - the hand-rolled version stranded a ~167 MB anonymous
volume per run for as long as this docstring said only "point ``DB_*`` at a
throwaway".

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
    reason="needs a real MariaDB; run `make test-mariadb` (or set RUN_ALEMBIC_ROUNDTRIP=1 + DB_*)",
)


def _seed(engine) -> None:
    """Put a row in the tables the data migrations actually touch.

    The roundtrip ran against an EMPTY schema, so every data migration's UPDATE
    matched zero rows and the whole class of "the DDL is fine, the backfill is
    wrong" was outside the gate (audit #2). One row per touched table is enough:
    what is being exercised is the statement, not the volume.
    """
    import sqlalchemy as sa

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (email, password_hash, display_name, role, "
                "is_disabled, email_verified, locale, created_at) VALUES "
                "('roundtrip@test.invalid', 'x', 'Roundtrip', 'employee', 0, 1, "
                "'en', NOW())"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO shares (id, created_by_id, kind, state, created_at) "
                "SELECT '00000000-0000-0000-0000-0000000rt001', id, 'outbound', "
                "'active', NOW() FROM users WHERE email = 'roundtrip@test.invalid'"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO files (id, share_id, original_filename, mime_type, "
                "size_bytes, state, uploaded_by_id, created_at) SELECT "
                "'00000000-0000-0000-0000-0000000rt002', "
                "'00000000-0000-0000-0000-0000000rt001', 'big.bin', "
                "'application/octet-stream', 3000000000, 'clean', id, NOW() "
                "FROM users WHERE email = 'roundtrip@test.invalid'"
            )
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


@_SKIP
def test_a_data_migration_runs_against_rows_that_exist():
    """The roundtrip above proves the DDL. It cannot prove a BACKFILL: it runs
    against an empty schema, so every data migration's UPDATE matches zero rows
    and the whole class of "the DDL is fine, the backfill is wrong" was outside
    the gate (audit #2) - a silent early `return` in one shipped this way.

    Step back over just the backfill revision (its downgrade is deliberately a
    no-op, so the table and the rows survive) and forward again, with a row that
    the backfill must touch.
    """
    import sqlalchemy as sa
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    from app.config import settings

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    upgrade(cfg, "head")

    engine = sa.create_engine(settings.database_url)
    try:
        _seed(engine)
        # 202607300001 adds files.av_unscanned; 202607310001 backfills it.
        downgrade(cfg, "202607300001")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE files SET av_unscanned = 0 WHERE id = "
                    "'00000000-0000-0000-0000-0000000rt002'"
                )
            )
        upgrade(cfg, "head")
        with engine.connect() as conn:
            flagged = conn.execute(
                sa.text(
                    "SELECT av_unscanned FROM files WHERE id = "
                    "'00000000-0000-0000-0000-0000000rt002'"
                )
            ).scalar()
        assert flagged == 1, (
            "a 3 GB clean file came through the backfill unflagged - the UPDATE "
            "matched nothing, which is what an empty schema always did"
        )
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM files WHERE id = '00000000-0000-0000-0000-0000000rt002'")
            )
            conn.execute(
                sa.text("DELETE FROM shares WHERE id = '00000000-0000-0000-0000-0000000rt001'")
            )
            conn.execute(
                sa.text("DELETE FROM users WHERE email = 'roundtrip@test.invalid'")
            )
        engine.dispose()


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


@_SKIP
def test_model_string_widths_match_the_migrated_schema():
    """`test_models_match_the_migrated_schema` above cannot see this.

    It is configured with `compare_type: False` ("MariaDB type reflection is
    noisy") and only looks at `_STRUCTURAL_OPS` - add/remove table/column. So a
    model that says String(512) against a migration that created VARCHAR(255)
    is invisible to it, in both directions.

    That matters more than it looks. Several write paths now derive their clip
    width from the MODEL (`_LAST_PATH_MAX`, `_AUDIT_TARGET_ID_MAX`, the ip
    clips, `error_log._W`) precisely so a literal cannot drift. If the model and
    the production schema disagree, those clips are computed against the wrong
    number and MariaDB raises DataError anyway - the exact failure they exist to
    prevent, with the guard pointing the wrong way.

    Enum columns are skipped: their length is derived from the member values,
    not declared, so a reflected difference is noise rather than drift."""
    from alembic.command import upgrade
    from alembic.config import Config
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy import String, create_engine
    from sqlalchemy import inspect as sa_inspect

    import app.models  # noqa: F401  - registers every model on Base.metadata
    from app.config import settings
    from app.database import Base

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    upgrade(cfg, "head")

    engine = create_engine(settings.database_url)
    try:
        insp = sa_inspect(engine)
        live_tables = set(insp.get_table_names())
        mismatches: list[str] = []
        compared = 0
        for table in Base.metadata.sorted_tables:
            if table.name in _IGNORED_TABLES or table.name not in live_tables:
                continue
            live_cols = {c["name"]: c for c in insp.get_columns(table.name)}
            for col in table.columns:
                declared = getattr(col.type, "length", None)
                if not declared or isinstance(col.type, SAEnum):
                    continue
                if not isinstance(col.type, String):
                    continue
                live = live_cols.get(col.name)
                if live is None:
                    continue  # a MISSING column is the other test's finding
                live_len = getattr(live["type"], "length", None)
                if live_len is None:
                    continue  # reflected as TEXT/LONGTEXT - unbounded, fine
                compared += 1
                if live_len != declared:
                    mismatches.append(
                        f"{table.name}.{col.name}: model String({declared}), "
                        f"migrated schema {live['type']}"
                    )
    finally:
        engine.dispose()

    # Anti-vacuity: reflection returning nothing useful, or every column being
    # skipped, would leave this green while checking nothing.
    assert compared > 50, (
        f"only {compared} bounded String columns were compared - the width "
        "check has stopped examining the schema"
    )
    assert not mismatches, (
        "the models and the migrated schema disagree on VARCHAR widths, so any "
        "clip derived from the model is computed against the wrong number:\n  "
        + "\n  ".join(mismatches)
    )
