"""Migrations must survive being run twice. Nothing checked that they do.

MariaDB auto-commits DDL and alembic does not bump `alembic_version` until a
revision RETURNS, so a crash partway through leaves the database partly migrated
and the revision is retried from the top. That retry is the case the `_has_*`
guards exist for - and three revisions guarded the wrong granularity:

schema-3  202605031600 wrapped the users.email NOT NULL + UNIQUE tightening
          inside `if not _has_column(...)`, so a crash between the add and the
          tighten left the column nullable and NOT UNIQUE forever: the retry saw
          the column and skipped everything. The uniqueness the whole migration
          exists to establish was silently absent, on the login identity column.
schema-8  four revisions created indexes inside the create_table guard (or
          behind an early `return`), including the UNIQUE index on
          analytics_snapshots.snapshot_date that snapshot_storage_today's
          idempotency depends on.
schema-2  202606130001 rendered Markdown to HTML with `html: False`, which
          ESCAPES existing HTML - so a second pass turned the imprint page into
          visible tag soup. Its docstring asserted the opposite.
schema-11 CLAUDE.md and alembic/env.py both said revisions import the shared
          guards. Zero did; there were seven divergent local copies, one of
          which RAISES on a missing table where the others return False.

These run against SQLite: they call `upgrade()` twice with a bind that already
has the objects, which is exactly the retry shape, without needing MariaDB.
The full-chain roundtrip lives in test_alembic_roundtrip.py.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
import sqlalchemy as sa

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


# --- P25: the generic nesting check, for revisions nobody has written yet ----


def _calls_in(node) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _create_guards(test) -> set[str]:
    """Guards that mean "it is ABSENT, so make it" - `if not _has_table(...)`.

    Only the NEGATED form is a create guard, and the distinction is the whole
    precision of this check. `if _has_column(...): op.alter_column(...)` reruns
    happily, because its body runs whenever the column exists; a positive check
    is idempotent by construction. Treating both alike flags three correctly
    guarded ops in 202605031600_email_plaintext."""
    found: set[str] = set()
    for n in ast.walk(test):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            found |= _calls_in(n.operand)
    return found & {"_has_table", "_has_column"}


_PROTECTING = {"_has_index", "_column_nullable"}
_NESTABLE = {"create_index", "create_unique_constraint", "alter_column"}


def _find_nested_ops(
    node, *, creating: bool, protected: bool, out: list, where: str
) -> None:
    if isinstance(node, ast.If):
        names = _calls_in(node.test)
        for sub in node.body:
            _find_nested_ops(
                sub,
                creating=creating or bool(_create_guards(node.test)),
                protected=protected or bool(names & _PROTECTING),
                out=out,
                where=where,
            )
        for sub in node.orelse:
            _find_nested_ops(
                sub, creating=creating, protected=protected, out=out, where=where
            )
        return
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _NESTABLE
        and creating
        and not protected
    ):
        out.append(f"{where}:{node.lineno}::{node.func.attr}")
    for child in ast.iter_child_nodes(node):
        _find_nested_ops(
            child, creating=creating, protected=protected, out=out, where=where
        )


def test_no_revision_nests_an_index_or_not_null_inside_a_create_guard():
    """Guard each op SEPARATELY, in every revision - including the next one.

    A `create_index` nested inside the `if not _has_table(...)` that creates the
    table is skipped FOREVER if the run crashes between them: the table now
    exists, the guard is False, and the body never runs again. The coverage
    above names by hand the three revisions that once got this wrong - it cannot
    see a migration nobody has written yet, which is exactly where the mistake
    gets made. CLAUDE.md's Conventions section claimed this file already failed
    on that; it does now.

    Green today: the scan finds no offender across every revision."""
    offenders: list[str] = []
    for f in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        _find_nested_ops(
            tree, creating=False, protected=False, out=offenders, where=f.name
        )

    assert not offenders, (
        "an op is nested inside a create guard, so a crash between the two "
        f"skips it forever on the retry: {offenders}"
    )


# --- schema-11: one definition, not seven -----------------------------------


def test_no_revision_defines_its_own_guard():
    offenders = []
    for f in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in (
                "_has_table", "_has_column", "_has_index", "_column_nullable"
            ):
                offenders.append(f"{f.name}::{node.name}")
    assert not offenders, (
        "local guard copies are back - they are what diverged into seven "
        f"variants: {offenders}"
    )


def test_every_guard_user_imports_the_shared_module():
    missing = []
    for f in sorted(VERSIONS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        uses = any(g in src for g in ("_has_table(", "_has_column(", "_has_index("))
        if uses and "from app.db_guards import" not in src:
            missing.append(f.name)
    assert not missing, missing


def test_a_guard_on_a_missing_table_answers_false_rather_than_raising():
    """The divergence that mattered: the `sa.inspect(bind).get_columns(table)`
    variant raises NoSuchTableError, which in a guard reads as a crash rather
    than "not there yet"."""
    from app.db_guards import has_column, has_index, has_table

    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        assert has_table(conn, "nope") is False
        assert has_column(conn, "nope", "col") is False
        assert has_index(conn, "nope", "ix") is False


def test_column_nullable_reads_the_current_state():
    from app.db_guards import column_nullable

    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE t (a TEXT, b TEXT NOT NULL)"))
        assert column_nullable(conn, "t", "a") is True
        assert column_nullable(conn, "t", "b") is False
        assert column_nullable(conn, "t", "absent") is False


# --- schema-3 / schema-8: the retry ------------------------------------------


def _apply(revision_module: str, setup_sql: list[str], *, times: int = 2):
    """Apply one revision's upgrade() against a SQLite database prepared by
    `setup_sql`, `times` times over. Returns (engine, connection).

    `setup_sql` is how the interrupted state is expressed: the point of these
    tests is not "running twice from scratch is safe" - that was always true,
    since the outer guard skips everything - but "resuming from the state a
    crash actually leaves behind finishes the job"."""
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    for stmt in setup_sql:
        conn.execute(sa.text(stmt))
    conn.commit()

    mod = importlib.import_module(f"alembic.versions.{revision_module}")
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        for _ in range(times):
            mod.upgrade()
            conn.commit()
    return engine, conn


def _run_twice(revision_module: str, setup_sql: list[str]):
    return _apply(revision_module, setup_sql, times=2)


@pytest.fixture(autouse=True)
def _versions_importable(monkeypatch):
    """`alembic/versions` is a script directory, not a package. Load the
    revisions under a synthetic package name so they can be imported."""
    import importlib.util
    import sys
    import types

    pkg = types.ModuleType("alembic_versions_under_test")
    pkg.__path__ = [str(VERSIONS)]
    sys.modules.setdefault("alembic.versions", pkg)
    for f in VERSIONS.glob("*.py"):
        name = f"alembic.versions.{f.stem}"
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, f)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    yield


_EMAIL_FRESH = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, email_hash TEXT, email_hint TEXT)",
    "CREATE TABLE invite_tokens (id INTEGER PRIMARY KEY, email_hash TEXT, "
    "email_hint TEXT)",
    "CREATE TABLE login_attempts (id INTEGER PRIMARY KEY, email_hash TEXT)",
]

# The state a crash between add_column and the NOT NULL + UNIQUE tightening
# leaves behind on MariaDB, where each DDL statement has already committed.
_EMAIL_INTERRUPTED = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(254), "
    "email_hash TEXT, email_hint TEXT)",
    "CREATE TABLE invite_tokens (id INTEGER PRIMARY KEY, email VARCHAR(254), "
    "email_hash TEXT, email_hint TEXT)",
    "CREATE TABLE login_attempts (id INTEGER PRIMARY KEY, email VARCHAR(254), "
    "email_hash TEXT)",
    "INSERT INTO users (id) VALUES (1)",
]


def test_a_resumed_email_migration_still_establishes_uniqueness():
    """schema-3, the actual failure. The retry saw `users.email` already there
    and skipped the whole block, so the column stayed nullable and NOT UNIQUE -
    permanently, because no later revision revisits it. Two accounts could then
    hold the same address on the login identity column."""
    engine, conn = _apply("202605031600_email_plaintext", _EMAIL_INTERRUPTED, times=1)
    from app.db_guards import column_nullable, has_index

    assert has_index(conn, "users", "ix_users_email"), (
        "the UNIQUE index on users.email was never created"
    )
    assert column_nullable(conn, "users", "email") is False, (
        "users.email is still nullable"
    )
    assert has_index(conn, "invite_tokens", "ix_invite_tokens_email")
    assert has_index(conn, "login_attempts", "ix_login_attempts_email")
    conn.close()


def test_the_resumed_backfill_does_not_clobber_real_addresses():
    """Control: the placeholder backfill runs unguarded now, so it has to stay
    `WHERE email IS NULL` - a second pass must not rename a live user to
    legacy-N@placeholder.invalid."""
    engine, conn = _apply(
        "202605031600_email_plaintext",
        _EMAIL_INTERRUPTED + [
            "UPDATE users SET email = 'real@person.example' WHERE id = 1"
        ],
        times=2,
    )
    assert conn.execute(
        sa.text("SELECT email FROM users WHERE id = 1")
    ).scalar() == "real@person.example"
    conn.close()


def test_the_email_migration_still_works_from_scratch():
    """Control: the fresh-install path is the one every new deployment takes."""
    engine, conn = _run_twice("202605031600_email_plaintext", _EMAIL_FRESH)
    from app.db_guards import column_nullable, has_column, has_index

    assert has_index(conn, "users", "ix_users_email")
    assert column_nullable(conn, "users", "email") is False
    assert has_column(conn, "users", "email_hash") is False, (
        "the legacy hash column should have been dropped"
    )
    conn.close()


# Tables created, indexes not yet - the state a crash inside the table guard
# leaves on MariaDB.
_INDEX_CASES = [
    (
        "202606070001_analytics_snapshots",
        "analytics_snapshots",
        ["ix_analytics_snapshots_snapshot_date"],
        "CREATE TABLE analytics_snapshots (id INTEGER PRIMARY KEY, "
        "snapshot_date DATE NOT NULL, storage_bytes BIGINT NOT NULL, "
        "files_clean INTEGER NOT NULL, files_infected INTEGER NOT NULL, "
        "files_total INTEGER NOT NULL, created_at TIMESTAMP NOT NULL)",
    ),
    (
        "202605030001_cron_runs",
        "cron_runs",
        ["ix_cron_runs_job_name", "ix_cron_runs_started_at"],
        "CREATE TABLE cron_runs (id INTEGER PRIMARY KEY, job_name VARCHAR(64) "
        "NOT NULL, started_at TIMESTAMP NOT NULL, completed_at TIMESTAMP, "
        "status VARCHAR(20) NOT NULL, result_summary TEXT, error_msg TEXT, "
        "duration_ms INTEGER)",
    ),
]


@pytest.mark.parametrize(
    "module,table,indexes,create", _INDEX_CASES,
    ids=[c[1] for c in _INDEX_CASES],
)
def test_a_resumed_run_creates_the_indexes_it_skipped(module, table, indexes, create):
    """schema-8. Created inside the table guard - or behind an early `return` -
    these were created once or never. A retry after the table landed saw the
    table and stopped, so the index simply never existed."""
    engine, conn = _apply(module, [create], times=1)
    from app.db_guards import has_index

    for ix in indexes:
        assert has_index(conn, table, ix), (
            f"{ix} was skipped because the table already existed"
        )
    conn.close()


@pytest.mark.parametrize(
    "module,table,indexes,create", _INDEX_CASES,
    ids=[c[1] for c in _INDEX_CASES],
)
def test_the_fresh_path_still_creates_both(module, table, indexes, create):
    """Control: hoisting the indexes must not lose them on a clean install."""
    engine, conn = _run_twice(module, [])
    from app.db_guards import has_index

    for ix in indexes:
        assert has_index(conn, table, ix)
    conn.close()


def test_the_unique_snapshot_index_is_actually_unique():
    """It is the only thing making snapshot_storage_today idempotent; a
    non-unique recreation would be a silent downgrade."""
    engine, conn = _run_twice("202606070001_analytics_snapshots", [])
    rows = conn.execute(sa.text("PRAGMA index_list(analytics_snapshots)")).fetchall()
    unique = {r[1]: r[2] for r in rows}
    assert unique.get("ix_analytics_snapshots_snapshot_date") == 1
    conn.close()


# --- schema-2 ----------------------------------------------------------------


def test_legal_pages_are_not_escaped_by_a_second_conversion():
    """The defect: `html: False` escapes existing HTML, so the imprint page came
    back as literal &lt;p&gt; tag soup - on the public page a visitor reads."""
    engine, conn = _run_twice(
        "202606130001_richtext_html",
        [
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "is_encrypted INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP NOT NULL, "
            "updated_by_id INTEGER)",
            "CREATE TABLE email_template_override (id INTEGER PRIMARY KEY, "
            "body_markdown TEXT NOT NULL)",
            "INSERT INTO app_settings (key, value, updated_at) "
            "VALUES ('legal.imprint_en', '# Imprint', '2026-01-01 00:00:00')",
        ],
    )
    value = conn.execute(
        sa.text("SELECT value FROM app_settings WHERE key = 'legal.imprint_en'")
    ).scalar()
    assert "&lt;" not in value, f"the second pass escaped the HTML: {value!r}"
    assert "<h1>" in value
    conn.close()


def test_the_marker_records_that_the_conversion_ran():
    engine, conn = _run_twice(
        "202606130001_richtext_html",
        [
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "is_encrypted INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP NOT NULL, "
            "updated_by_id INTEGER)",
            "CREATE TABLE email_template_override (id INTEGER PRIMARY KEY, "
            "body_markdown TEXT NOT NULL)",
        ],
    )
    assert conn.execute(
        sa.text("SELECT value FROM app_settings WHERE key = 'legal.richtext_migrated'")
    ).scalar() == "1"
    conn.close()


def test_markdown_still_converts_on_the_first_pass():
    """Control: this migration has a job to do, and skipping it would leave
    every legal page rendering raw Markdown."""
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(sa.text(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "is_encrypted INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP NOT NULL, "
        "updated_by_id INTEGER)"
    ))
    conn.execute(sa.text(
        "CREATE TABLE email_template_override (id INTEGER PRIMARY KEY, "
        "body_markdown TEXT NOT NULL)"
    ))
    conn.execute(sa.text(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('legal.privacy_de', '**fett**', '2026-01-01 00:00:00')"
    ))
    conn.execute(sa.text(
        "INSERT INTO email_template_override (id, body_markdown) "
        "VALUES (1, 'Hello [RESET_URL]')"
    ))
    conn.commit()

    mod = importlib.import_module("alembic.versions.202606130001_richtext_html")
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()
        conn.commit()

    assert "<strong>fett</strong>" in conn.execute(
        sa.text("SELECT value FROM app_settings WHERE key = 'legal.privacy_de'")
    ).scalar()
    body = conn.execute(
        sa.text("SELECT body_html FROM email_template_override WHERE id = 1")
    ).scalar()
    assert "[RESET_URL]" in body, "the placeholder token was mangled"
    conn.close()
