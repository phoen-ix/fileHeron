"""SQLAlchemy 2.0 engine + session factory + Base.

Pattern:
    from app.database import get_db
    @router.get(...)
    def handler(db: Session = Depends(get_db)):
        ...
"""
from collections.abc import Generator

from sqlalchemy import create_engine
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
