"""Quota, quarantine and reclaim: the file-lifecycle paths that lost track.

files-2   `expire_files` inlined its own purge loop instead of calling
          `delete_file_for_expiry`, so it never picked up that helper's
          `was_infected` guard. It unlinked the QUARANTINE locator - destroying
          evidence an admin can otherwise release or inspect - and released the
          same bytes a second time, silently inflating the uploader's quota.
files-8   the post-commit purge failure path claimed orphan-reclaim would mop
          up, but reclaim works from DB rows and the row is already `deleted`
          by then, so nothing could ever see the locator again.
files-10  `quarantine_file` revoked only `active` shares, so a share in
          `pending_approval` whose content failed AV stayed unannotated and an
          approver could flip it live.
flow-approval-10  rejected shares' bytes were reclaimed by nothing: rejection
          keeps the files on purpose so the owner can resubmit, but if they
          never did, the bytes sat against the uploader's quota indefinitely.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.workers import reclaim_orphaned_files as reclaim


@pytest.fixture
def owner(db, make_user):
    return make_user(email="owner@test.local", role=UserRole.employee)


# --- files-2: the double release -------------------------------------------


def test_expire_does_not_release_quota_twice_for_an_infected_file(
    db, owner, monkeypatch, tmp_path
):
    """quarantine.py already released these bytes when it moved the file. The
    inlined loop released them again, crediting the uploader for storage they
    never freed."""
    from app.services import file as file_svc
    from app.workers import expire_files as ef
    released = []
    monkeypatch.setattr(file_svc, "release_bytes", lambda **kw: released.append(kw))

    from datetime import timedelta

    from app.utils.timeutil import utc_now

    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=utc_now() - timedelta(hours=1),
    )
    db.add(sh)
    db.commit()
    q = tmp_path / "quarantined.bin"
    q.write_bytes(b"infected")
    db.add(
        File(
            share_id=sh.id, original_filename="bad.exe", size_bytes=999,
            storage_path=str(q), state=FileState.infected, uploaded_by_id=owner.id,
        )
    )
    db.commit()

    deleted = []
    monkeypatch.setattr(
        file_svc, "get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(lambda loc: deleted.append(loc))})(),
    )

    asyncio.run(ef.expire_files(None))

    assert released == [], "quota released a second time for a quarantined file"
    assert deleted == [], "the quarantine copy was unlinked, destroying the evidence"


def test_a_clean_file_still_releases_its_quota(db, owner, monkeypatch, tmp_path):
    """Control: the guard must not stop ordinary expiry from freeing space."""
    from datetime import timedelta

    from app.services import file as file_svc
    from app.utils.timeutil import utc_now
    from app.workers import expire_files as ef
    released = []
    monkeypatch.setattr(file_svc, "release_bytes", lambda **kw: released.append(kw))
    monkeypatch.setattr(
        file_svc, "get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(lambda loc: None)})(),
    )

    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=utc_now() - timedelta(hours=1),
    )
    db.add(sh)
    db.commit()
    p = tmp_path / "ok.bin"
    p.write_bytes(b"fine")
    db.add(
        File(
            share_id=sh.id, original_filename="ok.bin", size_bytes=4,
            storage_path=str(p), state=FileState.clean, uploaded_by_id=owner.id,
        )
    )
    db.commit()

    asyncio.run(ef.expire_files(None))
    assert len(released) == 1 and released[0]["bytes_to_free"] == 4


# --- files-8: the leak with no reclaim path --------------------------------


def test_a_failed_purge_leaves_a_durable_record(db, owner, monkeypatch, tmp_path):
    """The row is already `deleted` by the time the unlink runs, so reclaim can
    never see it. Without a record the bytes leak silently, charged to nobody."""
    from datetime import timedelta

    from app.services import file as file_svc
    from app.utils.timeutil import utc_now
    from app.workers import expire_files as ef

    monkeypatch.setattr(file_svc, "release_bytes", lambda **kw: None)

    def _boom(_loc):
        raise OSError("disk gone")

    monkeypatch.setattr(
        file_svc, "get_storage_backend",
        lambda: type("B", (), {"delete": staticmethod(_boom)})(),
    )

    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=utc_now() - timedelta(hours=1),
    )
    db.add(sh)
    db.commit()
    p = tmp_path / "leaky.bin"
    p.write_bytes(b"bytes")
    db.add(
        File(
            share_id=sh.id, original_filename="leaky.bin", size_bytes=5,
            storage_path=str(p), state=FileState.clean, uploaded_by_id=owner.id,
        )
    )
    db.commit()

    asyncio.run(ef.expire_files(None))

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_purge_failed.value)
        .all()
    )
    assert len(rows) == 1, "the leaked locator was not recorded anywhere"
    assert str(p) in (rows[0].extra or {}).get("locator", "")


# --- flow-approval-10: rejected shares ---------------------------------------


def test_rejected_is_a_terminal_state_for_reclaim():
    assert ShareState.rejected in reclaim._TERMINAL, (
        "a rejected share's bytes are reclaimed by nothing"
    )


def test_active_is_not_reclaimable():
    """Control: reclaim must never touch a live share."""
    assert ShareState.active not in reclaim._TERMINAL


def test_reject_stamps_a_terminal_time():
    """reclaim ages off `terminated_at`; without the stamp the state change
    alone is not enough."""
    import inspect

    from app.services import share as share_svc

    src = inspect.getsource(share_svc.reject_share)
    assert "terminated_at" in src


# --- files-10: quarantine and the approval queue ----------------------------


def test_quarantine_revokes_a_pending_approval_share():
    import inspect

    from app.services import quarantine

    src = inspect.getsource(quarantine)
    idx = src.index("share.state in (")
    window = src[idx : idx + 160]
    assert "pending_approval" in window, (
        "an approver can still flip a share live whose content failed AV"
    )
