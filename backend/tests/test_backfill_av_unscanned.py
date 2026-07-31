"""The backfill only touches rows clamd provably never read.

202607300001 declined to backfill `files.av_unscanned`, on the grounds that it
"cannot know which historical files were oversize at the time they were
scanned". That reasoning holds for the band between an operator's configured
`AV_MAX_SCAN_BYTES` and clamd's own ceiling - those files really were scanned.

It does not hold above the ceiling. clamd clamps its own MaxFileSize to INT_MAX
whatever clamd.conf says, so past 2147483645 bytes it has never read a file on
any version this product shipped. No past configuration could have changed
that, which is precisely what the original objection assumed it could not know.

The distinction is the whole test: flag above the ceiling, never below it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

CEILING = 2147483645


def _run_backfill(conn) -> int:
    """The migration's statement, run against the test connection."""
    result = conn.execute(
        text(
            "UPDATE files SET av_unscanned = 1 "
            "WHERE state = 'clean' AND av_unscanned = 0 "
            "AND size_bytes > :ceiling"
        ),
        {"ceiling": CEILING},
    )
    return result.rowcount or 0


@pytest.fixture
def rows(db, make_user, tmp_path):
    """One file per interesting size/state combination."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole

    owner = make_user(email="bf@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()

    made = {}
    cases = [
        ("above_clean", CEILING + 1, FileState.clean, False),
        ("at_ceiling_clean", CEILING, FileState.clean, False),
        ("below_clean", 1024, FileState.clean, False),
        ("above_already_flagged", CEILING + 1, FileState.clean, True),
        ("above_infected", CEILING + 1, FileState.infected, False),
        ("above_deleted", CEILING + 1, FileState.deleted, False),
        ("above_unscanned_state", CEILING + 1, FileState.ready_unscanned, False),
    ]
    for i, (name, size, state, flagged) in enumerate(cases):
        f = File(
            id=f"00000000-0000-0000-0000-0000000bf{i:03d}",
            share_id=sh.id,
            original_filename=f"{name}.bin",
            mime_type="application/octet-stream",
            size_bytes=size,
            storage_path=str(tmp_path / f"{name}.bin"),
            state=state,
            av_unscanned=flagged,
            uploaded_by_id=owner.id,
        )
        db.add(f)
        made[name] = f.id
    db.flush()
    db.commit()
    return made


def _flagged(db, file_id) -> bool:
    from app.models.file import File

    db.expire_all()
    return bool(db.query(File).filter(File.id == file_id).one().av_unscanned)


def test_a_clean_row_above_the_ceiling_is_flagged(db, rows):
    """The point: clamd answered `clean` without ever opening this file, on
    every version that could have written the row."""
    assert not _flagged(db, rows["above_clean"])
    _run_backfill(db.connection())
    assert _flagged(db, rows["above_clean"])


def test_a_clean_row_below_the_ceiling_is_left_alone(db, rows):
    """The original migration's objection, honoured. These files really were
    handed to clamd - under whatever limit was configured at the time - so
    flagging them would be a lie in the other direction."""
    _run_backfill(db.connection())
    assert not _flagged(db, rows["below_clean"])


def test_a_row_exactly_at_the_ceiling_is_left_alone(db, rows):
    """`> ceiling`, not `>=`. `CLAMD_MAX_FILE_SIZE` is the largest size clamd
    still reads, so a file of exactly that size was scanned."""
    _run_backfill(db.connection())
    assert not _flagged(db, rows["at_ceiling_clean"])


@pytest.mark.parametrize(
    "case", ["above_infected", "above_deleted", "above_unscanned_state"]
)
def test_only_clean_rows_are_touched(db, rows, case):
    """`infected` and `deleted` are verdicts of their own, and
    `ready_unscanned` has not been decided yet - the AV worker owns that row and
    will set the flag itself. Rewriting any of them would destroy information."""
    _run_backfill(db.connection())
    assert not _flagged(db, rows[case])


def test_running_it_twice_changes_nothing(db, rows):
    """Migrations are retried after a crash mid-DDL, so this has to converge."""
    first = _run_backfill(db.connection())
    second = _run_backfill(db.connection())
    assert first == 1, f"expected exactly one affected row, got {first}"
    assert second == 0
    assert _flagged(db, rows["above_clean"])
    assert _flagged(db, rows["above_already_flagged"])


def test_the_migration_uses_the_same_rule_as_this_test():
    """The statement under test is copied into this file, so pin it to the real
    one - a migration and its test drifting apart is how the thing it is
    repairing shipped in the first place."""
    import pathlib

    for base in (pathlib.Path("/repo/backend"), pathlib.Path(__file__).resolve().parents[1]):
        mig = base / "alembic/versions/202607310001_backfill_av_unscanned.py"
        if mig.exists():
            break
    src = mig.read_text()
    assert "state = 'clean' AND av_unscanned = 0" in src
    assert "AND size_bytes > :ceiling" in src
    assert "_CLAMD_MAX_FILE_SIZE = 2147483645" in src


def test_the_downgrade_does_not_unflag():
    """Clearing the flag would re-assert that these files were scanned, which
    was never true. A schema downgrade must not restore a false claim about
    what the antivirus did."""
    import pathlib

    for base in (pathlib.Path("/repo/backend"), pathlib.Path(__file__).resolve().parents[1]):
        mig = base / "alembic/versions/202607310001_backfill_av_unscanned.py"
        if mig.exists():
            break
    body = mig.read_text().split("def downgrade()")[1]
    assert "UPDATE" not in body.upper()
