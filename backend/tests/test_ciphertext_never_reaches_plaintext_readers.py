"""Ciphertext must never reach a reader that expects plaintext.

Handed to clamd's path scan, an encrypted file scans "clean" - antivirus
switched off without a sound. Handed to a download, it serves noise. So the raw
byte readers of the storage backend (`.open`, `.local_path`) are referenced only
by the modules that check `enc_version` first, pinned GENERICALLY over every
module in app/, and the scan entry point is checked to stream the plaintext.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from app.middleware.errors import AppError
from app.services import av_scan, file_encryption
from app.services import storage_backend as sb
from tests._encryption_helpers import encrypt_row, store_plain

APP = Path(__file__).resolve().parents[1] / "app"

# Modules allowed to touch a backend's raw readers, and why each is safe.
RAW_READERS = {
    "services/storage_backend.py": "defines them; serve_response branches on the cipher",
    "services/file_encryption.py": "open_plaintext: the plaintext branch of the one reader",
    "services/av_scan.py": "scan_stored: the plaintext branch, after checking the cipher",
    "services/zip_stream.py": "the plaintext member branch, after checking the cipher",
    "services/config_backup.py": "the branding logo, which is never encrypted (public by design)",
}


def _raw_reader_refs() -> set[str]:
    found: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr in {"open", "local_path"}):
                continue
            recv = node.value
            name = recv.id if isinstance(recv, ast.Name) else (
                ast.unparse(recv.func) if isinstance(recv, ast.Call) else ""
            )
            if "backend" in name:
                found.add(str(path.relative_to(APP)))
    return found


def test_only_the_cipher_aware_modules_touch_raw_bytes():
    refs = _raw_reader_refs()
    assert "services/storage_backend.py" in refs, "the scan went vacuous"
    stray = sorted(refs - set(RAW_READERS))
    assert not stray, (
        f"{stray} read stored bytes directly; go through "
        "file_encryption.open_plaintext (or check enc_version first and add the "
        "module here with the reason)"
    )


def _file_row(db, make_user, data: bytes):
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.utils.timeutil import utc_now

    u = make_user(email=f"u{os.urandom(3).hex()}@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active, expires_at=utc_now())
    db.add(sh)
    db.flush()
    f = File(share_id=sh.id, original_filename="a.bin", size_bytes=len(data),
             uploaded_by_id=u.id, state=FileState.clean, storage_path=store_plain(data))
    db.add(f)
    db.commit()
    return f


def test_an_encrypted_file_is_scanned_as_plaintext_never_by_path(db, make_user, monkeypatch):
    data = os.urandom(3 * 1024 * 1024 + 5)
    f = _file_row(db, make_user, data)
    encrypt_row(db, f)

    def _no_path_scan(_p):
        raise AssertionError("ciphertext reached clamd's path scan")

    seen = bytearray()

    def _stream(fh):
        while chunk := fh.read(65536):
            seen.extend(chunk)
        return av_scan.ScanResult(state="clean", signature=None, raw="ok")

    monkeypatch.setattr(av_scan, "scan_path", _no_path_scan)
    monkeypatch.setattr(av_scan, "scan_stream", _stream)
    cipher = file_encryption.cipher_for_file(f)
    assert cipher is not None
    assert av_scan.scan_stored(sb.get_storage_backend(), f.storage_path, cipher).state == "clean"
    assert bytes(seen) == data


def test_a_plaintext_file_keeps_the_path_scan(db, make_user, monkeypatch):
    f = _file_row(db, make_user, b"hello")
    calls = []
    monkeypatch.setattr(av_scan, "scan_path", lambda p: calls.append(p) or av_scan.ScanResult("clean", None, "ok"))
    assert file_encryption.cipher_for_file(f) is None
    av_scan.scan_stored(sb.get_storage_backend(), f.storage_path, None)
    assert calls == [f.storage_path]


def test_open_plaintext_reads_both_kinds(db, make_user):
    data = os.urandom(5000)
    plain = _file_row(db, make_user, data)
    enc = _file_row(db, make_user, data)
    encrypt_row(db, enc)
    backend = sb.get_storage_backend()
    for row in (plain, enc):
        with file_encryption.open_plaintext(backend, row.storage_path, file_encryption.cipher_for_file(row)) as fh:
            assert fh.read() == data


def test_a_key_on_the_wrong_row_is_unreadable_not_served(db, make_user):
    a = _file_row(db, make_user, b"a" * 10)
    b = _file_row(db, make_user, b"b" * 10)
    encrypt_row(db, a)
    b.enc_version, b.key_encrypted = 1, a.key_encrypted
    db.commit()
    with pytest.raises(AppError) as exc:
        file_encryption.cipher_for_file(b)
    assert exc.value.code == "FILE_UNREADABLE"
