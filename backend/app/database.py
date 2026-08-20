"""SQLAlchemy 2.0 engine + session factory + Base.

Pattern:
    from app.database import get_db
    @router.get(...)
    def handler(db: Session = Depends(get_db)):
        ...
"""
import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_POOL_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT_SEC,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """All ORM models inherit from this."""


_AFTER_COMMIT_KEY = "_fh_after_commit"


def run_after_commit(db: Session, thunk) -> None:
    """Defer a non-transactional side-effect (e.g. enqueuing an email job,
    publishing an SSE event) until this session's NEXT successful commit; drop
    it on rollback. Prevents a rolled-back action from firing mail / pinging the
    bell for a row that never persisted - and stops the email worker from racing
    ahead of the producer's commit and finding no row (audit M8). `thunk` is a
    zero-arg callable."""
    db.info.setdefault(_AFTER_COMMIT_KEY, []).append(thunk)


_AFTER_ROLLBACK_KEY = "_fh_after_rollback"


def run_after_rollback(db: Session, thunk) -> None:
    """Register a COMPENSATING action for a side-effect that has already
    happened outside the transaction and cannot be rolled back with it.

    `run_after_commit` defers a side-effect until the data is durable. This is
    its mirror image, for the cases where the write must happen first: bytes
    landed on the storage backend before the row describing them was committed,
    so a rollback leaves a blob nothing references and no sweeper can find (the
    sweepers all walk DB rows). Cleared on a successful commit, fired on
    rollback (audit 2026-07-30). `thunk` is a zero-arg callable."""
    db.info.setdefault(_AFTER_ROLLBACK_KEY, []).append(thunk)


@event.listens_for(Session, "after_rollback")
def _fh_fire_after_rollback(session: Session) -> None:
    thunks = session.info.pop(_AFTER_ROLLBACK_KEY, None)
    if not thunks:
        return
    log = logging.getLogger("fileheron.after_rollback")
    for thunk in thunks:
        try:
            thunk()
        except Exception:
            log.exception("after-rollback compensation failed")


@event.listens_for(Session, "after_commit")
def _fh_fire_after_commit(session: Session) -> None:
    # A successful commit means the compensations are no longer wanted.
    session.info.pop(_AFTER_ROLLBACK_KEY, None)
    thunks = session.info.pop(_AFTER_COMMIT_KEY, None)
    if not thunks:
        return
    log = logging.getLogger("fileheron.after_commit")
    for thunk in thunks:
        try:
            thunk()
        except Exception:
            log.exception("after-commit side-effect failed")


@event.listens_for(Session, "after_rollback")
def _fh_drop_after_commit(session: Session) -> None:
    session.info.pop(_AFTER_COMMIT_KEY, None)


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def pool_stats() -> dict | None:
    """Snapshot of the QueuePool: configured size, live overflow, and how many
    connections are currently checked out. None for pools that don't expose
    these (e.g. SQLite's). Surfaced by /api/health and /api/metrics so a
    pool-exhaustion problem is visible before it manifests as latency."""
    pool = engine.pool
    # size()/overflow()/checkedout() are QueuePool's, not the Pool base class's -
    # SQLite in the test harness uses StaticPool, which has none of them.
    if not isinstance(pool, QueuePool):
        return None
    try:
        return {
            "size": pool.size(),
            "overflow": pool.overflow(),
            "checked_out": pool.checkedout(),
        }
    except Exception:
        return None
