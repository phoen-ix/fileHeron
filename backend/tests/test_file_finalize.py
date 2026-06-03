"""finalize_to_disk: tusd upload → permanent storage."""
from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

import pytest

from app.models.file import FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import file as file_svc


@pytest.fixture
def fake_tus_dirs(monkeypatch):
    """Point TUS_UPLOAD_DIR + STORAGE_ROOT at temp directories so the
    real /data paths in dev/prod are untouched."""
    with tempfile.TemporaryDirectory() as tus_dir, tempfile.TemporaryDirectory() as storage_root:
        from app.config import settings

        monkeypatch.setattr(settings, "TUS_UPLOAD_DIR", tus_dir)
        monkeypatch.setattr(settings, "STORAGE_ROOT", storage_root)
        yield Path(tus_dir), Path(storage_root)


def _make_share(make_user, db) -> Share:
    """Tiny share to anchor a file row."""
    from datetime import datetime, timedelta, timezone

    sender = make_user(email="hr@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local")
    s = Share(
        kind=ShareKind.outbound,
        subject=None,
        state=ShareState.active,
        created_by_id=sender.id,
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(days=1)).replace(
            tzinfo=None
        ),
    )
    db.add(s)
    db.commit()
    return s, sender, rec


def test_finalize_works_when_rename_succeeds(make_user, db, fake_tus_dirs):
    """Happy path: same filesystem → os.rename succeeds → file lands at
    storage_path, state flips, source is gone."""
    tus_dir, _storage_root = fake_tus_dirs
    share, sender, _rec = _make_share(make_user, db)

    file_row = file_svc.create_pending(
        db,
        share=share,
        uploader=sender,
        original_filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=4,
    )
    db.commit()

    tus_id = "tus_xyz"
    src = tus_dir / tus_id
    src.write_bytes(b"DATA")

    file_svc.finalize_to_disk(db, file=file_row, tus_upload_id=tus_id)
    db.commit()

    assert file_row.state == FileState.ready_unscanned
    assert file_row.storage_path is not None
    assert Path(file_row.storage_path).is_file()
    assert Path(file_row.storage_path).read_bytes() == b"DATA"
    assert not src.exists()


def test_finalize_falls_back_to_copy_on_exdev(
    make_user, db, fake_tus_dirs, monkeypatch
):
    """Cross-device link case (Docker bind mounts on different
    filesystems): os.rename raises EXDEV → shutil.move auto-falls
    back to copy + unlink. The file still lands at storage_path."""
    tus_dir, _storage_root = fake_tus_dirs
    share, sender, _rec = _make_share(make_user, db)

    file_row = file_svc.create_pending(
        db,
        share=share,
        uploader=sender,
        original_filename="big.iso",
        mime_type="application/octet-stream",
        size_bytes=4,
    )
    db.commit()

    tus_id = "tus_exdev"
    src = tus_dir / tus_id
    src.write_bytes(b"BIGD")

    # Force os.rename (called inside shutil.move) to raise EXDEV the
    # first time. shutil.move will catch and fall back to copy+unlink.
    real_rename = os.rename
    calls = {"n": 0}

    def fake_rename(s, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(s))
        return real_rename(s, d)

    monkeypatch.setattr(os, "rename", fake_rename)

    file_svc.finalize_to_disk(db, file=file_row, tus_upload_id=tus_id)
    db.commit()

    assert file_row.state == FileState.ready_unscanned
    assert Path(file_row.storage_path).is_file()
    assert Path(file_row.storage_path).read_bytes() == b"BIGD"
    # Source removed by the copy+unlink fallback.
    assert not src.exists()


def test_finalize_raises_when_upload_missing(make_user, db, fake_tus_dirs):
    share, sender, _rec = _make_share(make_user, db)

    file_row = file_svc.create_pending(
        db,
        share=share,
        uploader=sender,
        original_filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=1,
    )
    db.commit()

    from app.middleware.errors import AppError

    with pytest.raises(AppError) as exc:
        file_svc.finalize_to_disk(
            db, file=file_row, tus_upload_id="not_on_disk"
        )
    assert exc.value.code == "UPLOAD_MISSING"
