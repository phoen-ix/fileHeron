"""LocalFilesystemBackend round-trip (v1.21.0 storage abstraction)."""
from __future__ import annotations

import os
import stat
import tempfile

from app.services.storage_backend import LocalFilesystemBackend


def test_finalize_stores_a_mkstemp_file_readable_by_others(tmp_path, monkeypatch):
    """Every finalize caller that stages with mkstemp/NamedTemporaryFile hands
    over a 0600 file; clamd reads the stored path as its own user."""
    monkeypatch.setattr("app.config.settings.STORAGE_ROOT", str(tmp_path / "files"))
    fd, src = tempfile.mkstemp(dir=tmp_path, suffix=".part")
    os.write(fd, b"x")
    os.close(fd)
    assert stat.S_IMODE(os.stat(src).st_mode) == 0o600  # the precondition of the bug
    b = LocalFilesystemBackend()
    loc = b.generate_locator("file-id-mode")
    b.finalize(src, loc)
    assert stat.S_IMODE(os.stat(loc).st_mode) == 0o644


def test_local_backend_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.STORAGE_ROOT", str(tmp_path / "files"))
    monkeypatch.setattr("app.config.settings.QUARANTINE_DIR", str(tmp_path / "quarantine"))
    b = LocalFilesystemBackend()
    assert b.supports_disk_stats is True

    # finalize consumes a temp file into the deterministic locator.
    src = tmp_path / "src.part"
    src.write_bytes(b"hello world")
    loc = b.generate_locator("file-id-123")
    assert loc.startswith(str(tmp_path / "files"))
    b.finalize(str(src), loc)
    assert not src.exists()  # moved, not copied

    # read-side
    assert b.exists(loc)
    assert b.size(loc) == 11
    assert b.local_path(loc) == loc  # disk-backed → sendfile + clamd path-scan
    assert b.download_url(locator=loc, filename="x", mime_type="text/plain", ttl_sec=60) is None
    with b.open(loc) as fh:
        assert fh.read() == b"hello world"

    # quarantine move (in) then release-style move (back)
    qloc = b.quarantine_locator("share-1", "x.txt")
    assert qloc.startswith(str(tmp_path / "quarantine"))
    b.move(loc, qloc)
    assert not b.exists(loc)
    assert b.exists(qloc)

    # delete is idempotent
    b.delete(qloc)
    assert not b.exists(qloc)
    b.delete(qloc)  # second delete is a no-op, not an error


def test_exists_handles_empty_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.STORAGE_ROOT", str(tmp_path))
    b = LocalFilesystemBackend()
    assert b.exists("") is False
    assert b.exists(str(tmp_path / "nope.bin")) is False
