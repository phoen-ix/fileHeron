"""The conftest VARCHAR guard must actually be able to fail.

Production is MariaDB with STRICT_TRANS_TABLES; SQLite ignores VARCHAR widths.
`tests/conftest.py` registers a `before_flush` listener so every write path the
suite already exercises also asserts its `String(n)` columns fit. A guard that
cannot go red is the failure this repo keeps recording - the route walker that
found 0 routes, `vue-tsc --noEmit` that checked 0 files - so these tests pin
that it fires, that it names what broke, that it does not fire on a value that
fits, and that it leaves unbounded Text columns alone (reaching for a deferred
one would emit SQL from inside before_flush).
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect as sa_inspect

from app.models.audit_log import AuditLog
from app.models.email_log import EmailLog

_WIDTH = AuditLog.__table__.c.target_id.type.length


def _audit(target_id: str) -> AuditLog:
    return AuditLog(
        event_type="test_event",
        target_type="file_bytes",
        target_id=target_id,
        extra={},
    )


def test_the_guard_refuses_a_value_wider_than_its_column(db):
    db.add(_audit("x" * (_WIDTH + 1)))
    with pytest.raises(AssertionError, match="exceed its declared VARCHAR width"):
        db.flush()
    db.rollback()


def test_the_guard_names_the_column_and_the_actual_length(db):
    db.add(_audit("y" * (_WIDTH + 7)))
    with pytest.raises(AssertionError) as exc:
        db.flush()
    msg = str(exc.value)
    assert "AuditLog.target_id" in msg
    assert str(_WIDTH) in msg and str(_WIDTH + 7) in msg
    db.rollback()


def test_a_value_that_exactly_fits_is_allowed(db):
    """Off-by-one the other way: the bound is `>`, not `>=`."""
    db.add(_audit("z" * _WIDTH))
    db.flush()


def test_unbounded_text_columns_are_not_policed(db):
    """`email_log` bodies are Text/LONGTEXT (length None) and `deferred=True`."""
    row = EmailLog(
        recipient_email="x@test.local",
        subject="s",
        body_text="q" * 100_000,
        status="sent",
        via="direct",
    )
    db.add(row)
    db.flush()
    assert "body_text" not in sa_inspect(row).unloaded
