"""Schema-introspection guards shared by alembic revisions.

MariaDB auto-commits DDL and alembic does not bump `alembic_version` until a
revision returns, so a crash mid-revision leaves the database partly migrated.
On retry an unguarded op raises "table/column/index already exists" and the
deployment is stuck: the revision can neither complete nor be skipped. Every
revision therefore guards each op with one of these.

They live here rather than in `alembic/env.py` because a revision module cannot
import env.py - `alembic` resolves to the installed library - and rather than in
each revision because that is what produced seven subtly different copies, one
of which (`sa.inspect(bind).get_columns(table)`) RAISES on a missing table where
every other one returns False (audit 2026-07-30).

Nothing in here may grow app coupling: revisions outlive the code around them.
Introspection only, no model imports, no behaviour changes.
"""
from __future__ import annotations

import sqlalchemy as sa


def has_table(bind, table: str) -> bool:
    """True iff `table` exists in the current schema."""
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t LIMIT 1"
            ),
            {"t": table},
        ).fetchone()
        return row is not None
    row = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return row is not None


def has_column(bind, table: str, column: str) -> bool:
    """True iff `column` exists on `table`. False if the table itself is absent."""
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c LIMIT 1"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return row is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def has_index(bind, table: str, index: str) -> bool:
    """True iff an index named `index` exists on `table`."""
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND index_name = :i LIMIT 1"
            ),
            {"t": table, "i": index},
        ).fetchone()
        return row is not None
    row = bind.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:i AND tbl_name=:t"
        ),
        {"i": index, "t": table},
    ).fetchone()
    return row is not None


def column_nullable(bind, table: str, column: str) -> bool:
    """True iff `column` currently accepts NULL. False if it is absent - the
    caller is asking "do I still need to tighten this", and a column that does
    not exist does not need tightening."""
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return bool(row) and row[0] == "YES"
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    for r in rows:
        if r[1] == column:
            return not bool(r[3])  # notnull flag
    return False


# Revisions import these under their historical private names.
_has_table = has_table
_has_column = has_column
_has_index = has_index
_column_nullable = column_nullable
