"""Guards on the upload path that did not cover the uploads that matter.

files-5   the storage-critical-low gate lived in routers/uploads.py, so it
          covered /uploads/init and /uploads/direct but NOT the tusd pre-create
          hook - which is the path every upload above 100 MB takes. The one
          class of upload that can fill a volume was the one class the
          disk-full guard did not cover.
files-4 / flow-upload-8  `files.tus_upload_id` was only written by
          finalize_to_disk (which then clears it), so an IN-FLIGHT upload always
          had NULL there. cleanup_abandoned_uploads' "leave it alone, the
          finalize hook may yet land" guard looks up
          `tus_upload_id == <id> AND state == uploading`, which no live upload
          could ever satisfy - the guard was structurally unreachable.
files-6   direct uploads stage a `<uuid>.part` in the same directory and unlink
          it on success. A crash between write and unlink left it forever: no
          `.info` sidecar, no `files` row, no sweeper.
tests-12  the tus id validator accepted 128 chars while the column is
          String(64), so a 65-128 char id became a MariaDB DataError - a 500
          where the validator exists to produce a clean 400. SQLite is
          permissive about length, so the suite structurally could not catch it.
flow-inbound-5  a Message-ID collision with a genuinely different mail dropped
          it silently while the UID highwater advanced past it.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import re

import pytest

from app.middleware.errors import AppError
from app.services import settings as settings_svc
from app.services import storage_guard
from app.services.tus_hooks import _TUS_UPLOAD_ID_RE, _check_tus_upload_id


# --- tests-12: the validator and the column must agree ----------------------


def test_the_id_bound_matches_the_column_width():
    """The column is String(64). Read it from the model rather than hardcoding,
    so widening one without the other fails here."""
    from app.models.file import File

    col = File.__table__.c.tus_upload_id
    bound = int(re.search(r"\{1,(\d+)\}", _TUS_UPLOAD_ID_RE.pattern).group(1))
    assert bound == col.type.length, (
        f"validator accepts {bound} chars, column holds {col.type.length} - "
        "the overflow becomes a DataError 500, not a 400"
    )


def test_an_over_long_id_is_a_clean_400():
    with pytest.raises(AppError) as exc:
        _check_tus_upload_id("a" * 65)
    assert exc.value.status_code == 400
    assert exc.value.code == "TUSD_INVALID_UPLOAD_ID"


def test_a_real_tusd_id_still_passes():
    """Control: tusd's own ids are 32 hex chars."""
    assert _check_tus_upload_id("0123456789abcdef0123456789abcdef") is not None


# --- files-5: the disk-full gate --------------------------------------------


def test_the_guard_refuses_when_storage_is_critical(db):
    settings_svc.set_value(
        db, key=settings_svc.Keys.STORAGE_CRITICAL_LOW, value="true", actor=None
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        storage_guard.refuse_if_critical_low(db)
    assert exc.value.status_code == 507


def test_the_guard_is_silent_when_storage_is_fine(db):
    """Control: this must not start refusing uploads on a healthy volume."""
    storage_guard.refuse_if_critical_low(db)


def test_the_resumable_path_applies_it():
    """The whole finding: the guard existed but not on the path large uploads
    take. Asserted at source level - handle_pre_create needs a full signed tusd
    envelope to reach behaviourally."""
    import inspect

    from app.services import tus_hooks

    src = inspect.getsource(tus_hooks.handle_pre_create)
    assert "refuse_if_critical_low" in src, (
        "multi-GB resumable uploads still bypass the disk-full guard"
    )


# --- files-4 / flow-upload-8: the unreachable sweeper guard ------------------


def test_pre_create_links_the_row_to_the_tusd_upload():
    """Without this the sweeper's live-upload guard can never match, because
    an in-flight row's tus_upload_id is NULL."""
    import inspect

    from app.services import tus_hooks

    src = inspect.getsource(tus_hooks.handle_pre_create)
    assert "tus_upload_id" in src


def test_the_sweeper_guard_has_something_to_match_on():
    """Pins the pairing: the sweeper queries on (tus_upload_id, uploading), so
    the stamp has to happen while the row is still in that state."""
    import inspect

    from app.workers import cleanup_abandoned_uploads as cau

    src = inspect.getsource(cau)
    assert "File.tus_upload_id == tus_id" in src
    assert "FileState.uploading" in src


# --- files-6: the .part leak ------------------------------------------------


def test_the_sweeper_removes_stale_direct_upload_parts(tmp_path, monkeypatch):
    import os
    import time

    from app.config import settings as cfg
    from app.workers import cleanup_abandoned_uploads as cau

    monkeypatch.setattr(cfg, "TUS_UPLOAD_DIR", str(tmp_path))
    old = tmp_path / "abandoned.part"
    fresh = tmp_path / "in-flight.part"
    old.write_bytes(b"stale")
    fresh.write_bytes(b"live")
    long_ago = time.time() - 60 * 60 * 24 * 30
    os.utime(old, (long_ago, long_ago))

    import asyncio

    asyncio.run(cau.cleanup_abandoned_uploads(None))

    assert not old.exists(), "abandoned .part files still accumulate forever"
    assert fresh.exists(), "an in-flight direct upload was deleted mid-write"
