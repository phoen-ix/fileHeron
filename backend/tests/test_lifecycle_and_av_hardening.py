"""Byte lifecycle, quota accounting and the AV verdict - audit #2.

Every test here is one finding, and each names the accounting it protects.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.utils.http_range import parse_single_range

# --- the Range parser -------------------------------------------------------


@pytest.mark.parametrize("header", ["bytes=\xb2-", "bytes=-\xb2", "bytes=0-\xb2", "bytes=٠-"])
def test_a_non_ascii_digit_is_not_a_range(header):
    """`str.isdigit()` is True for the latin-1 superscript two and for Arabic-
    Indic digits, and `int()` then raises ValueError straight out of the route:
    a 500 with an error_log row and a `notify_admin_error` enqueue per request.
    So `Range: bytes=\\xb2-` let an unauthenticated caller holding any
    public-link token manufacture 5xx alerts at will and flood the error log, on
    every download, preview and ZIP route (audit #2)."""
    assert parse_single_range(header, 1000) is None


def test_ordinary_ranges_still_parse():
    r = parse_single_range("bytes=10-20", 1000)
    assert (r.start, r.end) == (10, 20)
    assert parse_single_range("bytes=-50", 1000).start == 950


# --- the payment mark -------------------------------------------------------


def test_the_paid_window_outlives_a_large_transfer():
    """30 minutes was the SERVING mark's TTL. A 9 GB archive on a 25 Mbit/s line
    takes ~50 minutes, so the mark expired mid-transfer and the resume was
    answered 410 PUBLIC_LINK_EXHAUSTED - the exact outcome flow-publiclink-5 was
    filed for."""
    from app.services import transfer_activity

    assert transfer_activity.PAID_TTL_SEC >= 6 * 3600


def test_the_payment_check_fails_closed(monkeypatch):
    """Failing open meant that for the duration of a Redis outage a public link
    with `downloads_remaining = 0` served the complete archive to anyone sending
    `Range: bytes=1-`, repeatedly, with nothing written down."""
    from app.services import transfer_activity

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    assert transfer_activity.was_download_paid("link:1:zip:abc") is False


def test_the_serving_mark_still_fails_open(monkeypatch):
    """The control. The two marks answer different questions and take opposite
    postures on purpose: a wrong answer here costs a paused download, not the
    budget."""
    from app.services import transfer_activity

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    assert transfer_activity.was_download_recent("some-file") is True


# --- deferred byte purge ----------------------------------------------------


@pytest.fixture
def one_file(db, make_user, tmp_path):
    owner = make_user(email="lc@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    blob = tmp_path / "doc.bin"
    blob.write_bytes(b"x" * 128)
    f = File(
        id="00000000-0000-0000-0000-00000000lc01",
        share_id=sh.id,
        original_filename="doc.bin",
        mime_type="application/octet-stream",
        size_bytes=128,
        storage_path=str(blob),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return owner, sh, f, blob


def test_a_deferred_delete_keeps_the_bytes_until_the_caller_commits(db, one_file):
    """Unlinking first meant a commit that then failed rolled the row back to
    `clean` with a `storage_path` pointing at nothing: the file shows as present
    in the admin browser, a download 500s out of FileResponse, and the next
    sweep releases the same bytes from the quota counter a second time."""
    from app.services import file as file_svc

    owner, sh, f, blob = one_file
    locator = file_svc.hard_delete(db, file=f, reason="user_request", purge=False)
    assert locator == str(blob)
    assert blob.exists(), "the bytes went before the transaction was durable"

    db.rollback()
    db.expire_all()
    assert db.query(File).filter(File.id == f.id).one().state == FileState.clean
    assert blob.exists(), "a rolled-back delete must not have destroyed anything"


def test_the_post_commit_purge_removes_them(db, one_file):
    from app.services import file as file_svc

    owner, sh, f, blob = one_file
    locator = file_svc.hard_delete(db, file=f, reason="user_request", purge=False)
    db.commit()
    file_svc.purge_locators([locator])
    assert not blob.exists()


def test_the_purge_helper_never_raises(db):
    from app.services import file as file_svc

    file_svc.purge_locators([None, "/nonexistent/nowhere.bin"])


# --- the GDPR receipt -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_erasure_receipt_survives_the_retention_prune(db, make_user):
    """`config_backup.apply_backup` is explicitly forbidden from destroying
    these rows, and the nightly prune deleted them on the ordinary retention
    clock - so a receipt asked for a year later answered 404 and nothing could
    reproduce the file count or the bytes."""
    from datetime import timedelta

    from app.utils.timeutil import utc_now
    from app.workers import prune_history

    old = utc_now() - timedelta(days=4000)
    db.add(
        AuditLog(
            event_type=AuditEventType.user_erased.value,
            target_type="user",
            target_id="7",
            created_at=old,
        )
    )
    db.add(
        AuditLog(
            event_type=AuditEventType.login_success.value,
            target_type="user",
            target_id="7",
            created_at=old,
        )
    )
    db.commit()

    await prune_history.prune_history(None)

    kinds = {r.event_type for r in db.query(AuditLog).all()}
    assert AuditEventType.user_erased.value in kinds, "the GDPR receipt was pruned"
    assert AuditEventType.login_success.value not in kinds, (
        "the control: ordinary rows past retention must still go"
    )


# --- the AV verdict ---------------------------------------------------------


def test_the_mid_scan_guard_reads_the_committed_row():
    """Under MariaDB's REPEATABLE READ a plain SELECT answers from the worker
    transaction's snapshot, so a `deleted` committed by the API connection while
    clamd was working was invisible: the guard could not fire, and
    `quarantine_file` then flipped a deleted row back to `infected`, revoked a
    share that still had other clean files in it, released the same bytes a
    second time and emailed the uploader about a file they had deleted."""
    import inspect

    from app.workers import av_scan

    src = inspect.getsource(av_scan.av_scan_file)
    clean_branch, _, infected_branch = src.partition('if result.state == "infected":')

    # The clean branch was always safe: a conditional UPDATE reads the latest
    # committed row by definition.
    assert "File.state == FileState.ready_unscanned" in clean_branch
    # The infected branch read the state with a plain SELECT, which under
    # REPEATABLE READ answers from the snapshot this transaction opened before
    # the scan started - so the guard could not fire at all.
    assert "with_for_update()" in infected_branch, (
        "a plain SELECT here cannot see the delete another connection committed"
    )


def test_an_unscanned_file_is_not_rendered_inline(db):
    """`clean` + `av_unscanned` means "no verdict": clamd clamps MaxFileSize to
    ~2 GiB, so anything larger is served with a badge and never opened by the
    scanner. Downloading it is the visitor's informed choice; rendering it into
    their PDF viewer, one anonymous click from a public link, is not."""
    import inspect

    from app.routers import files as files_router
    from app.routers import public as public_router

    assert "FILE_NOT_SCANNED" in inspect.getsource(files_router._assert_previewable_state)
    assert "FILE_NOT_SCANNED" in inspect.getsource(public_router.public_preview)


def test_the_download_path_still_serves_an_unscanned_file():
    """The control: the badge exists precisely so these files stay
    downloadable."""
    import inspect

    from app.routers import files as files_router

    src = inspect.getsource(files_router._assert_file_state_servable)
    assert "FILE_NOT_SCANNED" not in src
