"""scripts/restore_validate.py's check that files encrypted at rest decrypt.

The weekly restore drill copies the WORKING-TREE script into whatever backend
image the host runs - which may predate encryption at rest, with no service
module and no `enc_version` column. So the check must skip there, never fail or
crash, and the script must import nothing new at top level.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.utils import crypto, file_crypto
from tests._encryption_helpers import encrypt_row, store_plain

SCRIPT = next(p for p in (Path("/src/scripts/restore_validate.py"),
                          Path(__file__).resolve().parents[2] / "scripts" / "restore_validate.py")
              if p.is_file())


@pytest.fixture(scope="module")
def rv():
    spec = importlib.util.spec_from_file_location("restore_validate_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stored(db, make_user):
    u = make_user(email="rv@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()

    def _make(data: bytes) -> File:
        f = File(share_id=sh.id, original_filename="r.bin", size_bytes=len(data), uploaded_by_id=u.id,
                 state=FileState.clean, storage_path=store_plain(data, name="rv"))
        db.add(f)
        db.commit()
        return f

    return _make


def test_nothing_encrypted_skips(rv, db, stored, capsys):
    stored(b"plain")
    assert rv._check_encrypted_files(db) is True
    assert "SKIP: no files are encrypted at rest" in capsys.readouterr().out


def test_encrypted_files_that_decrypt_pass(rv, db, stored, capsys):
    for data in (os.urandom(file_crypto.CHUNK_SIZE * 2 + 1), b""):
        encrypt_row(db, stored(data))
    assert rv._check_encrypted_files(db) is True
    assert "PASS: all 2 sampled files encrypted at rest decrypt" in capsys.readouterr().out


def test_a_key_this_instance_cannot_unwrap_fails(rv, db, stored, capsys):
    """What a restore without the right .env looks like: the wrapped key does
    not open (here: one bound to another row, which fails the same way)."""
    f = stored(b"secret contents")
    encrypt_row(db, f)
    f.key_encrypted = crypto.wrap_file_key(file_crypto.new_data_key(), kind="file", row_id="someone-else")
    db.commit()
    assert rv._check_encrypted_files(db) is False
    out = capsys.readouterr().out
    assert "FAIL" in out and f.id in out


def test_a_schema_from_before_encryption_skips(rv, capsys):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE files (id VARCHAR(36) PRIMARY KEY, storage_path VARCHAR(512))"))
    with Session(engine) as old_db:
        assert rv._check_encrypted_files(old_db) is True
    assert "SKIP: this schema predates encryption at rest" in capsys.readouterr().out


def test_an_image_from_before_encryption_skips(rv, db, monkeypatch, capsys):
    import app.services

    monkeypatch.delattr(app.services, "file_encryption")
    monkeypatch.setitem(sys.modules, "app.services.file_encryption", None)
    assert rv._check_encrypted_files(db) is True
    assert "SKIP: this image predates encryption at rest" in capsys.readouterr().out


# Every module the script imports at top level must exist in an image from
# before encryption at rest (v2.23.0, the host's image when this was written).
_OLD_IMAGE_MODULES = {
    "app.database", "app.models.audit_log", "app.models.file", "app.models.oidc_provider",
    "app.models.public_link", "app.models.share", "app.models.share_recipient", "app.models.user",
}


def test_the_script_imports_nothing_new_at_top_level():
    tree = ast.parse(SCRIPT.read_text())
    top = {node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module}
    app_modules = {m for m in top if m.startswith("app")}
    assert app_modules, "vacuity: the scan found the script's imports"
    assert app_modules <= _OLD_IMAGE_MODULES, app_modules - _OLD_IMAGE_MODULES
