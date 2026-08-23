"""Production semantics the SQLite harness cannot express.

The application suite runs on SQLite + StaticPool. Production is MariaDB 11,
`utf8mb4/utf8mb4_unicode_ci`, with no `sql_mode` override (so
STRICT_TRANS_TABLES) and DATETIME columns that declare no fractional-second
precision. Three whole classes of behaviour therefore differ between what the
tests assert and what the product does, and none of them are visible to a
behavioural test on SQLite:

* **Collation.** `utf8mb4_unicode_ci` is case-INSENSITIVE, accent-INSENSITIVE
  and PAD SPACE. SQLite's `=` on TEXT is binary. So `josé@x.com` and
  `jose@x.com` are two rows in the test suite and ONE row in production - the
  second INSERT raises 1062.
* **DATETIME precision.** MariaDB stores whole seconds here (and ROUNDS, so a
  value can land up to 0.5s in the future); SQLite keeps microseconds. Any
  `ORDER BY <timestamp>` without a tiebreaker is therefore total-ordered in the
  tests and arbitrary in production whenever rows share a second.
* **VARCHAR width.** Covered by the conftest guard and
  `test_alembic_roundtrip.py::test_model_string_widths_match_the_migrated_schema`.

These run in the `alembic-roundtrip` CI job, against its disposable MariaDB.
They are documentation with a failing mode: nothing here asserts that the
current behaviour is DESIRABLE, only that it is what production does, so a
future change to `normalize_email` or to a column's collation shows up as a
deliberate edit rather than a surprise.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa

_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_ALEMBIC_ROUNDTRIP") != "1",
    reason="needs a real MariaDB; run `make test-mariadb` (or set RUN_ALEMBIC_ROUNDTRIP=1 + DB_*)",
)


@pytest.fixture(scope="module")
def mariadb():
    from alembic.command import upgrade
    from alembic.config import Config

    from app.config import settings

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    upgrade(cfg, "head")
    engine = sa.create_engine(settings.database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_user(conn, email: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, display_name, role, "
            "is_disabled, email_verified, locale, created_at) VALUES "
            "(:e, 'x', 'T', 'employee', 0, 1, 'en', NOW())"
        ),
        {"e": email},
    )


# --- collation --------------------------------------------------------------


@_SKIP
def test_the_email_column_is_accent_insensitive_in_production(mariadb):
    """`normalize_email` only strips and lowercases, so Python treats these as
    two addresses. MariaDB does not, and the UNIQUE key is what enforces it.

    NOT folded in `normalize_email`: per RFC the local part is accent-distinct,
    so folding would merge two genuinely different addresses in Python to paper
    over the database doing it. The semantically correct fix is a binary
    collation on this column - which is a migration, and a separate decision.
    """
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email LIKE '%@collation.test'"))
        _insert_user(conn, "jose@collation.test")

    with mariadb.connect() as conn:
        hit = conn.execute(
            sa.text("SELECT COUNT(*) FROM users WHERE email = :e"),
            {"e": "josé@collation.test"},
        ).scalar()
    assert hit == 1, (
        "utf8mb4_unicode_ci is expected to be accent-insensitive here; if this "
        "fails the column's collation changed and normalize_email's contract "
        "changed with it"
    )

    with pytest.raises(sa.exc.IntegrityError), mariadb.begin() as conn:
        _insert_user(conn, "josé@collation.test")

    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email LIKE '%@collation.test'"))


@_SKIP
def test_the_email_column_is_pad_space_in_production(mariadb):
    """`'x ' = 'x'` is TRUE in MariaDB and FALSE in SQLite, for every VARCHAR
    equality. `normalize_email` strips, so this is reachable only through a
    path that bypasses it."""
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email LIKE '%@pad.test'"))
        _insert_user(conn, "pad@pad.test")
    with mariadb.connect() as conn:
        hit = conn.execute(
            sa.text("SELECT COUNT(*) FROM users WHERE email = :e"),
            {"e": "pad@pad.test   "},
        ).scalar()
    assert hit == 1
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email LIKE '%@pad.test'"))


# --- DATETIME precision -----------------------------------------------------


@_SKIP
def test_datetime_columns_store_whole_seconds(mariadb):
    """No column in this schema declares fractional-second precision, so
    MariaDB truncates to whole seconds - which is what makes an unqualified
    `ORDER BY created_at` non-deterministic in production and stable in the
    tests. `users.sessions_invalidated_at` is compared at second granularity
    deliberately, and this is why."""
    insp = sa.inspect(mariadb)
    fractional = []
    for table in insp.get_table_names():
        for col in insp.get_columns(table):
            t = col["type"]
            if t.__class__.__name__.upper().startswith("DATETIME"):
                fsp = getattr(t, "fsp", None)
                if fsp:
                    fractional.append(f"{table}.{col['name']} (fsp={fsp})")
    assert not fractional, (
        "these columns now keep sub-second precision, so the second-granularity "
        f"comparisons and the ORDER BY tiebreakers may need revisiting: {fractional}"
    )
