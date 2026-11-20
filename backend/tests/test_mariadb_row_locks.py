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
        def _capture(_c, _cur, stmt, _p, _ctx, _many):
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


@_SKIP
def test_a_secret_reveal_locks_the_secret_row(mariadb, locked_user):
    """Secrets (v2.24.0): a reveal locks the secret row for its whole
    check-claim-decrypt-burn sequence. The view counters are conditional UPDATEs
    and would hold on their own; the lock is what keeps a concurrent last view,
    Burn now and the expiry sweep from interleaving between the eligibility
    check and the shred. Asserted on the emitted SQL, like the sites above."""
    import inspect

    from app.services import secret_reveal

    for fn in (secret_reveal.reveal_for_user, secret_reveal.reveal_by_token):
        assert "_lock(db" in inspect.getsource(fn), f"{fn.__name__} no longer locks"

    sid = "00000000-0000-4000-8000-0000000000a1"
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM secrets WHERE id = :i"), {"i": sid})
        conn.execute(
            sa.text(
                "INSERT INTO secrets (id, created_by_id, has_passphrase, view_scope, "
                "views_used, notify_on_view, state, created_at) "
                "SELECT :i, id, 0, 'per_person', 0, 0, 'active', NOW() "
                "FROM users WHERE email = :e"
            ),
            {"i": sid, "e": _EMAIL},
        )
    statements: list[str] = []
    conn = mariadb.connect()
    try:
        @sa.event.listens_for(conn, "before_cursor_execute")
        def _capture(_c, _cur, stmt, _p, _ctx, _many):
            statements.append(stmt)

        with Session(bind=conn) as db:
            assert secret_reveal._lock(db, sid) is not None
        assert any(
            "FOR UPDATE" in s.upper() and "FROM secrets" in s for s in statements
        ), statements
    finally:
        conn.close()
        with mariadb.begin() as c:
            c.execute(sa.text("DELETE FROM secrets WHERE id = :i"), {"i": sid})


@_SKIP
def test_answering_a_secret_request_locks_the_request_row(mariadb, locked_user):
    """Secret requests (v2.24.0): the answer path locks the request row before
    it checks state and expiry, so a concurrent answer, Cancel or the expiry
    sweep cannot interleave between the check and the claim. The claim itself
    is a conditional UPDATE and would hold alone - the lock is what keeps the
    answer secret from being written for a request that just closed. Asserted
    on the emitted SQL."""
    import inspect

    from app.services import secret_request

    assert "_lock(db" in inspect.getsource(secret_request.answer_request), (
        "answer_request no longer locks"
    )

    rid = "00000000-0000-4000-8000-0000000000b1"
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM secret_requests WHERE id = :i"), {"i": rid})
        conn.execute(
            sa.text(
                "INSERT INTO secret_requests (id, requester_id, label, state, expires_at, "
                "answer_max_views, created_at) "
                "SELECT :i, id, 'lock test', 'open', NOW() + INTERVAL 1 DAY, 1, NOW() "
                "FROM users WHERE email = :e"
            ),
            {"i": rid, "e": _EMAIL},
        )
    statements: list[str] = []
    conn = mariadb.connect()
    try:
        @sa.event.listens_for(conn, "before_cursor_execute")
        def _capture(_c, _cur, stmt, _p, _ctx, _many):
            statements.append(stmt)

        with Session(bind=conn) as db:
            assert secret_request._lock(db, rid) is not None
        assert any(
            "FOR UPDATE" in s.upper() and "FROM secret_requests" in s for s in statements
        ), statements
    finally:
        conn.close()
        with mariadb.begin() as c:
            c.execute(sa.text("DELETE FROM secret_requests WHERE id = :i"), {"i": rid})


@_SKIP
def test_changing_a_link_limit_locks_the_link_row(mariadb, locked_user):
    """v2.32.0: a limit change reads the counter and writes a new one, so a
    download's decrement landing between the two would be handed back. Asserted
    on the emitted SQL, like the sites above."""
    import inspect

    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import User
    from app.services import public_link as public_link_svc

    assert "with_for_update=True" in inspect.getsource(public_link_svc.update_limit)

    statements: list[str] = []
    conn = mariadb.connect()
    outer = conn.begin()
    try:
        @sa.event.listens_for(conn, "before_cursor_execute")
        def _capture(_c, _cur, stmt, _p, _ctx, _many):
            statements.append(stmt)

        with Session(bind=conn, join_transaction_mode="create_savepoint") as db:
            owner = db.query(User).filter(User.email == _EMAIL).one()
            sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
            db.add(sh)
            db.flush()
            link = public_link_svc.create_link(
                db, share=sh, actor=owner, password=None, download_limit=2,
                notify_on_download=False,
            ).record
            db.flush()
            statements.clear()
            public_link_svc.update_limit(
                db, actor=owner, share=sh, link=link, new_limit=1, clear=False
            )
        assert any(
            "FOR UPDATE" in s.upper() and "FROM public_links" in s for s in statements
        ), statements
    finally:
        outer.rollback()
        conn.close()


# ---- The share announcement: two writers, one decision ----------------------
# A share's recipient mail waits for two writes from two transactions: the
# owner's batch signal (`files-added`) and the scan flipping the last file to
# `clean`. Each writer then decides. On 2026-11-20 both decided "not yet" - the
# worker could not see the uncommitted batch signal, the request's REPEATABLE
# READ view predated the scan - and the mail waited for the minute sweep. Only a
# real MariaDB has read views, so only here can the interleaving be replayed.


@pytest.fixture
def owed_share(mariadb, locked_user):
    """A live share whose announcement is owed, with one file still being
    scanned. Committed, so a second connection sees it; removed afterwards."""
    from app.models.file import FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import User

    from ._share_helpers import land_file

    with Session(bind=mariadb) as db:
        owner = db.query(User).filter(User.email == _EMAIL).one()
        sh = Share(
            created_by_id=owner.id,
            kind=ShareKind.outbound,
            state=ShareState.active,
            notify_on_activation=True,
        )
        db.add(sh)
        db.flush()
        f = land_file(db, sh, owner, state=FileState.ready_unscanned)
        db.commit()
        ids = (sh.id, f.id)
    yield ids
    with mariadb.begin() as c:
        c.execute(sa.text("DELETE FROM audit_log WHERE target_id IN (:s, :f)"),
                  {"s": ids[0], "f": ids[1]})
        c.execute(sa.text("DELETE FROM shares WHERE id = :s"), {"s": ids[0]})


def _announced(mariadb, share_id) -> bool:
    with mariadb.connect() as c:
        owed = c.execute(
            sa.text("SELECT notify_on_activation FROM shares WHERE id = :s"), {"s": share_id}
        ).scalar_one()
    return owed is None


def _release(db, file_id) -> None:
    """What the worker does when clamd answers clean: flip, commit, decide."""
    from app.models.file import File
    from app.services import av_release
    from app.services import share as share_svc

    f = db.get(File, file_id)
    assert av_release.apply_verdict(db, f, av_release.VERDICT_CLEAN)
    db.commit()
    share_svc.notify_after_commit(db, f.share_id)


@_SKIP
def test_the_mail_goes_out_when_the_scan_and_the_batch_signal_cross(mariadb, owed_share):
    """The incident, replayed. The files-added request reads first (its view is
    fixed), the scan finishes and decides "not yet" (no batch signal), then the
    request records the batch signal and its in-transaction check reads the old
    view: "still scanning". Only the route's post-commit decision, in a fresh
    transaction, sees both."""
    from app.models.share import Share
    from app.models.user import User
    from app.routers.shares import files_added_route
    from app.schemas.share import FilesAddedRequest

    share_id, file_id = owed_share
    with (
        Session(bind=mariadb, expire_on_commit=False) as req,
        Session(bind=mariadb, expire_on_commit=False) as worker,
    ):
        owner = req.query(User).filter(User.email == _EMAIL).one()
        req.get(Share, share_id)

        _release(worker, file_id)
        assert not _announced(mariadb, share_id), "the worker must not see the batch yet"

        files_added_route(
            share_id=share_id,
            payload=FilesAddedRequest(file_ids=[file_id], notify=True),
            request=None,
            user=owner,
            db=req,
        )
    assert _announced(mariadb, share_id)


_APPROVER = "approver@rowlock.test"


@pytest.fixture
def approver(mariadb):
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email = :e"), {"e": _APPROVER})
        conn.execute(
            sa.text(
                "INSERT INTO users (email, password_hash, display_name, role, "
                "is_disabled, email_verified, locale, failed_login_count, created_at) "
                "VALUES (:e, 'x', 'Approver', 'admin', 0, 1, 'en', 0, NOW())"
            ),
            {"e": _APPROVER},
        )
    yield
    with mariadb.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE email = :e"), {"e": _APPROVER})


@_SKIP
def test_the_mail_goes_out_when_the_scan_finishes_during_the_approval(
    mariadb, owed_share, approver
):
    """The same crossing against an approval: the scan decides while the share
    is still pending, and approve_share's own check reads its older view."""
    from app.models.share import Share
    from app.models.user import User
    from app.routers.shares import approve_share_route
    from app.schemas.share import ApproveShareRequest
    from app.services import share_approval as approval_svc

    share_id, file_id = owed_share
    with mariadb.begin() as c:
        c.execute(
            sa.text(
                "UPDATE shares SET state = 'pending_approval', upload_batch_done = 1 "
                "WHERE id = :s"
            ),
            {"s": share_id},
        )
    with (
        Session(bind=mariadb, expire_on_commit=False) as req,
        Session(bind=mariadb, expire_on_commit=False) as worker,
    ):
        admin = req.query(User).filter(User.email == _APPROVER).one()
        fingerprint = approval_svc.content_fingerprint(req, req.get(Share, share_id))

        _release(worker, file_id)
        assert not _announced(mariadb, share_id), "a pending share is not announced"

        approve_share_route(
            share_id=share_id,
            request=None,
            payload=ApproveShareRequest(content_fingerprint=fingerprint),
            user=admin,
            db=req,
        )
    assert _announced(mariadb, share_id)


@_SKIP
def test_the_decision_never_reads_an_older_view_than_its_lock(mariadb, owed_share):
    """A caller may hand over a session with a transaction already open - the
    encryption lane's purge sweep reads between its commit and the notice. That
    view predates whatever committed meanwhile, so the decision ends it first."""
    from app.models.file import File
    from app.services import share as share_svc

    share_id, file_id = owed_share
    with Session(bind=mariadb) as stale:
        assert stale.get(File, file_id).state.value == "ready_unscanned"
        with mariadb.begin() as c:
            c.execute(sa.text("UPDATE files SET state = 'clean' WHERE id = :f"), {"f": file_id})
            c.execute(
                sa.text("UPDATE shares SET upload_batch_done = 1 WHERE id = :s"), {"s": share_id}
            )
        assert share_svc.notify_if_downloadable(stale, share_id) is True
        stale.commit()
    assert _announced(mariadb, share_id)


@_SKIP
def test_the_decision_locks_the_share_before_it_reads(mariadb, owed_share):
    """The lock is what makes the two deciders take turns, and it must come
    FIRST: the read view starts at the first plain SELECT after it."""
    from app.services import share as share_svc

    share_id, _ = owed_share
    statements: list[str] = []
    conn = mariadb.connect()
    try:
        @sa.event.listens_for(conn, "before_cursor_execute")
        def _capture(_c, _cur, stmt, _p, _ctx, _many):
            statements.append(stmt)

        with Session(bind=conn) as db:
            share_svc.notify_if_downloadable(db, share_id)
            db.rollback()
        first = statements[0]
        assert "FROM shares" in first and "FOR UPDATE" in first.upper(), statements
    finally:
        conn.close()
