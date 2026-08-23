"""The `SELECT … FOR UPDATE` sites, exercised where they actually do something.

Nine call sites in `app/` take a row-level write lock, and **not one of them is
tested behaviourally**. SQLite does not reject `FOR UPDATE` - SQLAlchemy's
`SQLiteCompiler.for_update_clause` returns `""`, compiling it away silently - so
production emits the lock at nine sites and the harness emits it at zero,
uniformly. Nothing in `backend/tests/` uses `threading`, `asyncio.gather` or
`concurrent.futures`, `conftest.py` binds every session to one `StaticPool`
connection, and e2e runs `workers: 1`: no two DB transactions are ever open at
once anywhere else in this repo.

What existed instead was source-text inspection - `test_erasure_residue.py`
greps for the substring, and `test_rate_limit.py`'s own docstring concedes it
"exercises the invariant … rather than true parallel contention".

Deterministic by construction, with no threads and no sleeps: connection A holds
the lock, connection B sets `innodb_lock_wait_timeout = 1` and must fail. The
assertion is on the ERROR, not on elapsed time.

Gated like the rest of the real-database suite and named explicitly in CI's
`alembic-roundtrip` step - a file that is not named there does not run.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_ALEMBIC_ROUNDTRIP") != "1",
    reason="needs a real MariaDB; run `make test-mariadb` (or set RUN_ALEMBIC_ROUNDTRIP=1 + DB_*)",
)

_EMAIL = "locks@rowlock.test"


@pytest.fixture(scope="module")
def mariadb():
    from pathlib import Path

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


@pytest.fixture
def locked_user(mariadb):
    """A real `users` row, sentinel-scoped so it cannot collide with the other
    real-database files sharing this schema."""
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email = :e"), {"e": _EMAIL})
        conn.execute(
            sa.text(
                "INSERT INTO users (email, password_hash, display_name, role, "
                "is_disabled, email_verified, locale, failed_login_count, created_at) "
                "VALUES (:e, 'x', 'Lock', 'employee', 0, 1, 'en', 0, NOW())"
            ),
            {"e": _EMAIL},
        )
    yield
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email = :e"), {"e": _EMAIL})


@_SKIP
def test_record_failure_locks_the_row_on_the_read(mariadb, locked_user):
    """The lock must be taken by the SELECT, not merely by the later UPDATE.

    That distinction is the whole defect. Without `FOR UPDATE`, six concurrent
    failures all READ the same pre-increment `failed_login_count`; each then
    writes 1, the counter advances by one instead of six, and the lockout
    threshold is never crossed. The UPDATE still serialises - so a test that
    only shows "a second caller blocks" passes even with the lock removed, which
    is exactly what an earlier version of this test did.

    Asserted on the SQL the ORM actually emits against the MariaDB dialect -
    not on source text, and not on timing."""
    from app.models.user import User
    from app.services import rate_limit

    statements: list[str] = []

    conn = mariadb.connect()
    try:
        @sa.event.listens_for(conn, "before_cursor_execute")
        def _capture(_c, _cur, stmt, _p, _ctx, _many):  # noqa: ANN001
            statements.append(stmt)

        with Session(bind=conn) as db:
            user = db.query(User).filter(User.email == _EMAIL).one()
            statements.clear()
            rate_limit.record_failure(db, user=user)

        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert selects, f"record_failure emitted no SELECT at all: {statements}"
        assert any("FOR UPDATE" in s.upper() for s in selects), (
            "record_failure re-read the row WITHOUT a row lock, so concurrent "
            f"failures can all see the same pre-increment count: {selects}"
        )
    finally:
        conn.close()


@_SKIP
def test_a_held_lock_really_does_block_a_second_locker(mariadb, locked_user):
    """The harness control: prove this MariaDB actually enforces row locks, so
    the emitted-SQL assertion above is about something real. A plain SELECT is
    NOT blocked (MVCC), a second `FOR UPDATE` is."""
    holder = mariadb.connect()
    try:
        holder.execute(sa.text("BEGIN"))
        holder.execute(
            sa.text("SELECT id FROM users WHERE email = :e FOR UPDATE"), {"e": _EMAIL}
        ).one()

        contender = mariadb.connect()
        try:
            contender.execute(sa.text("SET SESSION innodb_lock_wait_timeout = 1"))
            got = contender.execute(
                sa.text("SELECT email FROM users WHERE email = :e"), {"e": _EMAIL}
            ).scalar()
            assert got == _EMAIL, "a plain SELECT was blocked; the harness is wrong"

            with pytest.raises(sa.exc.OperationalError) as exc:
                contender.execute(
                    sa.text("SELECT id FROM users WHERE email = :e FOR UPDATE"),
                    {"e": _EMAIL},
                )
            assert "1205" in str(exc.value) or "Lock wait timeout" in str(exc.value)
        finally:
            contender.close()
    finally:
        holder.rollback()
        holder.close()


@_SKIP
def test_the_counter_is_correct_once_the_lock_is_released(mariadb, locked_user):
    """The other half: serialising must produce the RIGHT number, not merely
    refuse. Two sequential locked increments land at 2, not 1."""
    from app.models.user import User
    from app.services import rate_limit

    for _ in range(2):
        with Session(bind=mariadb) as db:
            user = db.query(User).filter(User.email == _EMAIL).one()
            rate_limit.record_failure(db, user=user)
            db.commit()

    with mariadb.connect() as conn:
        n = conn.execute(
            sa.text("SELECT failed_login_count FROM users WHERE email = :e"),
            {"e": _EMAIL},
        ).scalar()
    assert n == 2, f"expected 2 recorded failures, got {n}"
