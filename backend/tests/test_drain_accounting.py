"""The drain-before-update counters, and why they never reached zero.

`maintenance.pending_update` waits for `active_uploads + active_downloads == 0`
before restarting the stack. Four findings all made that number wrong in the
same direction - permanently non-zero - so a postponed update sat out its whole
deadline instead of firing when the stack was genuinely idle, and the admin
dialog showed phantom activity nobody could account for.

flow-maintenance-1  the download counter was released via
                    `FileResponse(background=...)`, and Starlette only runs a
                    BackgroundTask after a response has been SENT. An
                    unsatisfiable Range raises inside `FileResponse.__call__`
                    before anything is sent, so the entry registered a moment
                    earlier leaked until the 6-hour age prune. One
                    `curl -H 'Range: bytes=99999999-'` per phantom.
flow-maintenance-3  `active_uploads` was an unqualified
                    `COUNT(*) WHERE state = uploading`, and rows only leave that
                    state via the tusd post-finish hook - so a closed browser tab
                    counted for up to TUS_UPLOAD_ABANDONED_AFTER_HOURS (24).
flow-maintenance-9  preview streams called `serve_response` without `count=True`,
                    so they were invisible to the drain entirely.
download-7          the same, on the public preview route.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from app.config import settings
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import storage_backend
from app.services import transfer_activity as ta
from app.utils.timeutil import utc_now


@pytest.fixture
def share(db, make_user):
    owner = make_user(email="up@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.commit()
    return sh, owner


# --- flow-maintenance-3: the uploads counter --------------------------------


def test_an_abandoned_upload_stops_counting(db, share):
    """The defect: a row left `uploading` by a closed tab held the drain open
    for up to 24 hours."""
    sh, owner = share
    stale = utc_now() - timedelta(hours=settings.UPLOAD_STALE_AFTER_HOURS + 1)
    db.add(
        File(
            share_id=sh.id, original_filename="abandoned", size_bytes=1,
            state=FileState.uploading, uploaded_by_id=owner.id, created_at=stale,
        )
    )
    db.commit()
    assert ta.active_uploads(db) == 0


def test_a_live_upload_still_counts(db, share):
    """Control: the drain exists to wait for these."""
    sh, owner = share
    db.add(
        File(
            share_id=sh.id, original_filename="live", size_bytes=1,
            state=FileState.uploading, uploaded_by_id=owner.id,
        )
    )
    db.commit()
    assert ta.active_uploads(db) == 1


def test_a_direct_upload_without_a_tus_id_still_counts(db, share):
    """The finding proposed also filtering on `tus_upload_id IS NOT NULL`. That
    would have hidden every direct upload (up to 100 MB) and the window between
    /uploads/init and the tusd pre-create hook - re-creating, on the upload
    side, exactly the blind spot the preview fix closes on the download side."""
    sh, owner = share
    db.add(
        File(
            share_id=sh.id, original_filename="direct", size_bytes=1,
            state=FileState.uploading, uploaded_by_id=owner.id, tus_upload_id=None,
        )
    )
    db.commit()
    assert ta.active_uploads(db) == 1


# --- flow-maintenance-1: the downloads counter ------------------------------


@pytest.mark.asyncio
async def test_the_counter_is_released_when_the_response_never_sends(monkeypatch, tmp_path):
    """A response that raises before sending must still release its entry.
    Pre-fix the release rode on a BackgroundTask, which Starlette only runs
    after a successful send."""
    released = []
    monkeypatch.setattr(
        transfer_activity_module(), "download_finished", lambda i: released.append(i)
    )

    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    resp = storage_backend._CountedFileResponse(path=str(p), dl_id="dl-123")

    async def _boom(*_a, **_k):
        raise RuntimeError("range not satisfiable")

    monkeypatch.setattr(
        storage_backend.FileResponse, "__call__", _boom, raising=True
    )

    with pytest.raises(RuntimeError):
        await resp({}, None, None)

    assert released == ["dl-123"], "the drain entry leaked when the send failed"


def transfer_activity_module():
    from app.services import transfer_activity

    return transfer_activity


def test_the_release_is_not_a_background_task_any_more():
    """Pinning the mechanism, not just the outcome: reverting to
    `background=BackgroundTask(...)` reintroduces the leak silently."""
    src = inspect.getsource(storage_backend.serve_response)
    assert "BackgroundTask" not in src
    assert "_CountedFileResponse" in src


# --- flow-maintenance-9 / download-7: previews were invisible ---------------


@pytest.mark.parametrize("module", ["files", "public"])
def test_preview_streams_register_with_the_drain(module):
    """A 30 GB preview stream counted for nothing, so the drain could declare
    the stack idle and restart mid-transfer."""
    import importlib

    mod = importlib.import_module(f"app.routers.{module}")
    src = inspect.getsource(mod)
    idx = src.index("preview_svc.SECURITY_HEADERS")
    window = src[idx : idx + 200]
    assert "count=True" in window, (
        f"the {module} preview stream is still invisible to the drain"
    )
