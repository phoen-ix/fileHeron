"""Production semantics the SQLite harness cannot express.

The application suite runs on SQLite + StaticPool. Production is MariaDB 11,
`utf8mb4/utf8mb4_unicode_ci`, with no `sql_mode` override (so
STRICT_TRANS_TABLES) and DATETIME columns that declare no fractional-second
precision. Three whole classes of behaviour therefore differ between what the
tests assert and what the product does, and none of them are visible to a
behavioural test on SQLite:

* **Collation.** `utf8mb4_unicode_ci` is case-INSENSITIVE, accent-INSENSITIVE
  and PAD SPACE. SQLite's `=` on TEXT is binary. The email columns now carry
  `utf8mb4_bin` (migration 202609240001), because the insensitive match let
  forgot-password mail a reset link to a lookalike address; every other VARCHAR
  still compares insensitively.
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
def test_the_email_columns_compare_exactly_in_production(mariadb):
    """`jose@` and `josé@` are two addresses, and two mailboxes.

    This pinned the OPPOSITE until 2026-09-24 - "accent-insensitive, and the
    fix is a separate decision" - without noticing what the insensitive match
    did: forgot-password found `victim@example.com` for a request naming
    `victim@exämple.com` and mailed the reset link to the typed, lookalike
    address. The binary collation is that decision; services/user_lookup.py
    re-checks in Python so the application does not depend on it alone."""
    with mariadb.connect() as conn:
        collations = dict(
            conn.execute(
                sa.text(
                    "SELECT table_name, collation_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND column_name = 'email' "
                    "AND table_name IN ('users', 'invite_tokens')"
                )
            ).all()
        )
    assert collations == {"users": "utf8mb4_bin", "invite_tokens": "utf8mb4_bin"}

    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email LIKE '%@collation.test'"))
        _insert_user(conn, "jose@collation.test")

    with mariadb.connect() as conn:
        for lookalike in ("josé@collation.test", "JOSE@collation.test"):
            hit = conn.execute(
                sa.text("SELECT COUNT(*) FROM users WHERE email = :e"),
                {"e": lookalike},
            ).scalar()
            assert hit == 0, f"{lookalike!r} matched jose@collation.test"

    # A distinct address is a distinct row, not a UNIQUE violation.
    with mariadb.begin() as conn:
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
