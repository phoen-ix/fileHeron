"""Irreversible writes that happened before the transaction describing them.

data-11 / flow-upload-3  `finalize_to_disk` consumed the tusd working file - a
        rename, or a copy+unlink across filesystems - and only then set
        `storage_path` and flushed. A commit failure right after (a DB blip, a
        lock timeout, a dropped connection) left the bytes at the locator with
        the row still saying `uploading` and `storage_path=NULL`. Nothing could
        find them again: the abandoned-upload sweeper looks for tusd working
        files, which are gone, and reclaim_orphaned_files walks `files` rows,
        which pointed nowhere. The uploader stayed charged for bytes no one
        could serve or delete.

data-10 / flow-inbound-9  the same shape for inbound attachments: the blob went
        to the storage backend, the row was added, and the commit belonged to
        run_poll several layers up. A rollback there orphaned the blob with no
        sweeper able to see it.

tests-18  `analytics._STORED_STATES` and `quota._used_bytes_query` each declared
        the same list with a comment on each saying to keep them in step. A
        convention, not a guarantee: divergence would make the admin storage
        totals and the quota charged to a user disagree, with nothing failing.

admin-11 / dos-7  `_daily` materialised every row in the window to produce ~90
        integers.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

from app.database import run_after_rollback
from app.services import analytics, quota
from app.services import file as file_svc

# --- data-11 / flow-upload-3 -------------------------------------------------


def test_the_locator_is_recorded_before_the_bytes_move():
    """Ordering is the whole fix: the row must name the locator before the
    irreversible move, so a failure on either side leaves something findable."""
    src = inspect.getsource(file_svc.finalize_to_disk)
    record_at = src.index("file.storage_path = locator")
    move_at = src.index("backend.finalize(str(src), locator)")
    assert record_at < move_at, (
        "the tusd file is still consumed before the row knows where it went"
    )


def test_the_state_flip_still_happens_after_the_move():
    """Control: recording the intent early must not mark the file servable
    before its bytes are actually there."""
    src = inspect.getsource(file_svc.finalize_to_disk)
    move_at = src.index("backend.finalize(str(src), locator)")
    ready_at = src.index("file.state = FileState.ready_unscanned")
    assert move_at < ready_at


# --- data-10 / flow-inbound-9 ------------------------------------------------


def test_a_rollback_compensation_hook_exists():
    """`run_after_commit` defers a side-effect until the data is durable. Its
    mirror image is needed where the write must happen FIRST."""
    assert callable(run_after_rollback)


def test_a_rolled_back_session_fires_the_compensation(db):
    """The real shape: inbound_mail writes the blob, adds the row, and the
    commit belongs to run_poll several layers up. When that commit never
    happens, the compensation has to fire."""
    from app.models.user import User, UserRole
    from app.utils.crypto import argon2_hash, normalize_email

    fired = []
    # after_rollback only fires when there is an actual DBAPI transaction to
    # roll back, so the session needs UNCOMMITTED work - which is exactly the
    # situation this hook exists for.
    db.add(
        User(
            email=normalize_email("pending@test.local"),
            password_hash=argon2_hash("x"),
            display_name="Pending",
            role=UserRole.employee,
        )
    )
    db.flush()
    run_after_rollback(db, lambda: fired.append("dropped"))
    db.rollback()
    assert fired == ["dropped"], "the orphaned blob would have been left behind"


def test_a_committed_session_does_not(db, make_user):
    """Control: compensating a successful write would delete live data."""
    fired = []
    make_user(email="x@test.local")
    run_after_rollback(db, lambda: fired.append("dropped"))
    db.commit()
    assert fired == []


def test_inbound_attachments_register_the_compensation():
    from app.services import inbound_mail

    src = inspect.getsource(inbound_mail)
    assert "run_after_rollback" in src, (
        "an inbound attachment blob can still be orphaned by a failed poll commit"
    )


# --- tests-18 ---------------------------------------------------------------


def test_the_stored_state_list_has_one_definition():
    """Not 'the two lists happen to be equal today' - the same object."""
    assert analytics._STORED_STATES is quota.STORED_STATES


def test_quota_uses_the_shared_definition():
    src = inspect.getsource(quota._used_bytes_query)
    assert "STORED_STATES" in src
    assert "FileState.ready_unscanned" not in src, "the list was re-declared inline"


# --- admin-11 / dos-7 -------------------------------------------------------


def test_the_daily_bucket_query_streams():
    """A 90-day window on a busy instance must not land in memory to produce
    ninety integers."""
    src = inspect.getsource(analytics._daily)
    assert "yield_per" in src
    # Strip the docstring: it describes the old `.all()` behaviour, and matching
    # raw source flags the explanation as if it were the code. Same trap the
    # nginx config test hit.
    body = src.split('"""', 2)[-1]
    assert ".all()" not in body


def test_bucketing_stays_in_python():
    """Pinned deliberately. Moving it into SQL was tried: CONVERT_TZ is
    MariaDB-only, and shifting the column by a fixed offset and grouping on
    func.date() does not survive SQLite, which has no interval arithmetic and
    silently does numeric addition on the string instead."""
    src = inspect.getsource(analytics._daily)
    assert "astimezone(tz)" in src
