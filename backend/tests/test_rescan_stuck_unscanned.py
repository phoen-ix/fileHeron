"""L8/L7: cleanup_stale_uploads re-enqueues scans for files stuck in
ready_unscanned (a failed or missed scan).

The oversize exclusion this file used to assert - "excluding oversize files
(which clamd can't fully scan, so re-scanning would loop)" - was the defect,
not the feature. It is the only automated recovery there is, so excluding a
class of file from it made `ready_unscanned` permanent for that class: every
download answered 425 "try again shortly" about a scan that would never run
(audit 2026-07-30 residual sweep). `av_scan_file` now decides oversize before
scanning and releases such files in one pass, so re-enqueueing them terminates.
See test_oversize_scan_recovery.py.

Age is the only thing that gates this sweep now, which is the point.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.utils.timeutil import utc_now


def _seed_stuck(db, sender, *, fid, size_bytes, finalized_minutes_ago):
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=utc_now() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    f = File(
        id=fid,
        share_id=share.id,
        original_filename="u.bin",
        mime_type="application/octet-stream",
        size_bytes=size_bytes,
        state=FileState.ready_unscanned,
        storage_path="/x",
        uploaded_by_id=sender.id,
        finalized_at=utc_now() - timedelta(minutes=finalized_minutes_ago),
    )
    db.add(f)
    db.commit()
    return f


@pytest.mark.asyncio
async def test_rescan_requeues_stuck_unscanned(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    stuck = _seed_stuck(db, sender, fid="stuck", size_bytes=1000, finalized_minutes_ago=90)
    fresh = _seed_stuck(db, sender, fid="fresh", size_bytes=1000, finalized_minutes_ago=5)
    oversize = _seed_stuck(db, sender, fid="oversize", size_bytes=10**13, finalized_minutes_ago=90)

    calls: list = []

    async def _spy(name, *a, **k):
        calls.append((name, a))

    from app.services import job_queue
    monkeypatch.setattr(job_queue, "aenqueue", _spy)

    from app.workers.cleanup_stale_uploads import cleanup_stale_uploads
    result = await cleanup_stale_uploads(None)

    requeued = {a[0] for (name, a) in calls if name == "av_scan_file"}
    assert stuck.id in requeued
    assert fresh.id not in requeued        # finalized too recently - still in flight
    assert oversize.id in requeued, (
        "an oversize file was excluded from the only automated recovery there "
        "is, which is what made ready_unscanned permanent for it"
    )
    assert result["rescans_requeued"] == 2
