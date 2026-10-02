"""Migration 202610030001 (encryption at rest): additive, and its downgrade
refuses while any row is encrypted - dropping the columns would drop the keys."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

# alembic/versions is a script directory, not a package: load the revision by path.
_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "202610030001_encryption_at_rest.py"
_spec = importlib.util.spec_from_file_location("rev_202610030001", _PATH)
assert _spec and _spec.loader
_MOD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_MOD)


def _engine_with(enc_value):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE files (id TEXT PRIMARY KEY, enc_version SMALLINT)"))
        conn.execute(sa.text("CREATE TABLE inbound_attachments (id INTEGER PRIMARY KEY, enc_version SMALLINT)"))
        conn.execute(sa.text("INSERT INTO files (id, enc_version) VALUES ('f1', NULL)"))
        if enc_value is not None:
            conn.execute(sa.text(f"INSERT INTO files (id, enc_version) VALUES ('f2', {int(enc_value)})"))
    return engine


def test_the_downgrade_refuses_while_a_file_is_encrypted():
    engine = _engine_with(1)
    with engine.connect() as conn, pytest.raises(RuntimeError, match="1 encrypted row"):
        _MOD._refuse_if_encrypted(conn)
    engine.dispose()


def test_the_downgrade_proceeds_when_nothing_is_encrypted():
    engine = _engine_with(None)
    with engine.connect() as conn:
        _MOD._refuse_if_encrypted(conn)  # does not raise
    engine.dispose()


def test_the_new_columns_are_null_for_existing_rows(db, make_user):
    """Every existing row reads as plaintext: the new columns default to NULL."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.utils.timeutil import utc_now

    u = make_user(email="u@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active, expires_at=utc_now())
    db.add(sh)
    db.flush()
    f = File(share_id=sh.id, original_filename="a", size_bytes=1, uploaded_by_id=u.id, state=FileState.clean)
    db.add(f)
    db.commit()
    db.refresh(f)
    assert (f.enc_version, f.key_encrypted, f.release_verdict) == (None, None, None)
