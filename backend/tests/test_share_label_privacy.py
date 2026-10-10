"""A share's label never names a file still awaiting review.

`_effective_subject` labels a share that has no subject with its first file's
name - in the inbox list and on the share page, recipients included. It took
`files[0]` from `Share.files`, which has no ORDER BY, so on MariaDB (UUID
primary keys, ties broken in index order) the label could be a file added after
approval and still pending its own review - whose name `_to_share_response`
withholds from recipients, because a filename is content. The label is now the
earliest file by (created_at, id) that is not pending review.

Unit-level on purpose: SQLite returns the relationship in insertion order, so a
route test would pass with the bug in place.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.file import FileApprovalState
from app.routers.shares import _effective_subject

_T0 = datetime(2026, 10, 10, 9, 0, 0)


def _file(fid: str, name: str, minutes: int, state=FileApprovalState.approved):
    return SimpleNamespace(
        id=fid,
        original_filename=name,
        created_at=_T0 + timedelta(minutes=minutes),
        approval_state=state,
    )


def test_the_label_skips_a_file_awaiting_review():
    pending = _file("0", "merger-terms-unreleased.pdf", 30, FileApprovalState.pending_review)
    released = _file("1", "agenda.pdf", 0)
    assert _effective_subject(SimpleNamespace(subject=None), [pending, released]) == "agenda.pdf"


def test_the_label_is_the_earliest_file_in_whatever_order_the_rows_come():
    rows = [_file("c", "later.pdf", 5), _file("b", "same-second-b.pdf", 0), _file("a", "same-second-a.pdf", 0)]
    share = SimpleNamespace(subject=None)
    assert _effective_subject(share, rows) == "same-second-a.pdf"
    assert _effective_subject(share, list(reversed(rows))) == "same-second-a.pdf"


def test_a_subject_wins_and_nothing_releasable_means_no_label():
    assert _effective_subject(SimpleNamespace(subject="Q3"), []) == "Q3"
    only_pending = [_file("x", "secret.pdf", 0, FileApprovalState.pending_review)]
    assert _effective_subject(SimpleNamespace(subject=None), only_pending) == ""
