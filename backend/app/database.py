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


@event.listens_for(Session, "after_commit")
def _fh_fire_after_commit(session: Session) -> None:
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


def get_db() -> Generator[Session, None, None]:
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
    if not hasattr(pool, "size"):
        return None
    try:
        return {
            "size": pool.size(),
            "overflow": pool.overflow(),
            "checked_out": pool.checkedout(),
        }
    except Exception:
        return None
