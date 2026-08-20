"""Four defects around who can act, and what is written down when they do.

data-5    `expire_share_now` and `invalidate_all_active_shares` unlinked bytes
          and released quota BEFORE the caller's commit - the exact ordering the
          hourly cron was restructured away from in audit M14. A commit failure
          left a row still marked `clean` over bytes that were already gone:
          silent data loss the UI cannot show, plus a second quota release on
          the next pass.
download-5 an approver reviewing a pending share got the complete original
          bytes with no download_log row and no audit entry. The exemption
          exists so review does not spend the recipients' download budget; it
          had been written as "do not record this at all", so a person who is
          not a recipient could take every file in a share and leave nothing
          behind.
flow-approval-5 turning share approval off stranded every share already in the
          queue. Senders cannot withdraw them, nothing sweeps them, and
          `can_approve` returned False for everyone - including admins.
flow-onboarding-7 register-from-invite minted its session inline instead of
          through the login funnel, so `known_devices` was never seeded and the
          user's first real sign-in - same browser, minutes later - fired a "new
          device" security alert about itself.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole


@pytest.fixture
def owner(db, make_user):
    return make_user(email="owner@test.local", role=UserRole.employee)


def _share_with_file(db, owner, tmp_path, *, state=ShareState.active, fid="f1"):
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=state)
    db.add(sh)
    db.flush()
    path = tmp_path / f"{fid}.bin"
    path.write_bytes(b"payload")
    f = File(
        id=f"00000000-0000-0000-0000-0000000000{fid}", share_id=sh.id,
        original_filename=f"{fid}.bin", mime_type="application/octet-stream",
        size_bytes=7, storage_path=str(path), state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return sh, f, path


# --- data-5 ------------------------------------------------------------------


def test_expire_now_does_not_unlink_before_the_caller_commits(db, owner, tmp_path):
    from app.services import share as share_svc

    sh, f, path = _share_with_file(db, owner, tmp_path, fid="a1")
    _, to_purge = share_svc.expire_share_now(db, user=owner, share=sh, request=None)

    assert path.exists(), "bytes were destroyed before the transaction committed"
    assert len(to_purge) == 1, "the caller was given nothing to purge"


def test_the_row_is_marked_deleted_inside_the_transaction(db, owner, tmp_path):
    """Phase 1 must still do the transactional half, or a rollback and a commit
    would be indistinguishable."""
    from app.services import share as share_svc

    sh, f, path = _share_with_file(db, owner, tmp_path, fid="a2")
    share_svc.expire_share_now(db, user=owner, share=sh, request=None)

    assert f.state == FileState.deleted
    assert sh.state == ShareState.expired


def test_a_rollback_leaves_the_file_intact(db, owner, tmp_path):
    """The failure this ordering exists to prevent: the state flip is lost, so
    the bytes must still be there."""
    from app.services import share as share_svc

    sh, f, path = _share_with_file(db, owner, tmp_path, fid="a3")
    share_svc.expire_share_now(db, user=owner, share=sh, request=None)
    db.rollback()

    db.expire_all()
    assert path.exists()
    assert db.query(File).filter(File.id == f.id).one().state == FileState.clean
    assert db.query(Share).filter(Share.id == sh.id).one().state == ShareState.active


def test_phase_two_actually_removes_the_bytes(db, owner, tmp_path):
    """Control: deferring the purge must not mean skipping it."""
    from app.services import file as file_svc
    from app.services import share as share_svc

    sh, f, path = _share_with_file(db, owner, tmp_path, fid="a4")
    _, to_purge = share_svc.expire_share_now(db, user=owner, share=sh, request=None)
    db.commit()
    file_svc.purge_expired_bytes(db, to_purge, reason="expire_now")

    assert not path.exists()


def test_an_infected_file_is_not_purged_or_re_credited(db, owner, tmp_path):
    """Its bytes are in quarantine and its quota was released when it was moved
    there; both must be left alone."""
    from app.services import file as file_svc

    sh, f, path = _share_with_file(db, owner, tmp_path, fid="a5")
    f.state = FileState.infected
    db.commit()

    entry = file_svc.mark_deleted_for_expiry(db, file=f)
    assert entry is not None and entry[0] is None, (
        "the quarantine locator was queued for deletion"
    )

    released = []
    _orig = file_svc.release_bytes
    try:
        file_svc.release_bytes = lambda **kw: released.append(kw)
        file_svc.purge_expired_bytes(db, [entry], reason="t")
    finally:
        file_svc.release_bytes = _orig
    assert released == [], "the uploader was credited for bytes they never freed"
    assert path.exists(), "the quarantined evidence was destroyed"


def test_the_routers_purge_only_after_committing():
    from app.routers import shares as shares_router

    for fn in (shares_router.expire_share_now_route, shares_router.bulk_expire):
        src = inspect.getsource(fn)
        assert "purge_expired_bytes" in src, f"{fn.__name__} never purges"
        assert src.index("db.commit()") < src.index("purge_expired_bytes"), (
            f"{fn.__name__} still purges before committing"
        )


def test_config_restore_purges_after_its_own_committed_pass():
    """Byte deletion must follow the commit that marks the shares invalid.

    A rollback brings back neither the rows nor the bytes, so unlinking first
    would leave shares still marked active over files that are gone.

    This reads `apply_backup`'s SOURCE because the alternative is running a
    destructive import to observe the ordering. `str.index` was used before and
    raised ValueError - an error, not a failing assertion - the moment any of
    the three moved, so a refactor got a stack trace instead of a verdict.
    """
    from app.services import config_backup

    src = inspect.getsource(config_backup.apply_backup)
    inv = src.find("invalidate_all_active_shares")
    assert inv != -1, (
        "apply_backup no longer mentions invalidate_all_active_shares - this "
        "test has stopped pinning anything. If the phase moved into a helper, "
        "point the scan at that helper rather than deleting the check."
    )
    commit = src.find("db.commit()", inv)
    purge = src.find("purge_expired_bytes", inv)
    assert commit != -1, "no db.commit() after the share invalidation"
    assert purge != -1, "no purge_expired_bytes after the share invalidation"
    assert inv < commit < purge, (
        "order must be invalidate -> commit -> purge; found "
        f"invalidate@{inv}, commit@{commit}, purge@{purge}"
    )


# --- download-5 --------------------------------------------------------------


PW = "correct horse battery staple"


def _enable_approval(db):
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.SHARE_APPROVAL_ENABLED, value="true", actor=None
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW,
        value="true", actor=None,
    )
    db.commit()


@pytest.mark.asyncio
async def test_an_approver_review_download_is_recorded(
    db, make_user, client, login_as, monkeypatch, tmp_path
):
    """The defect: a person who is not a recipient could pull every file in a
    share and leave nothing behind - no download_log row, no audit entry."""
    make_user(email="boss@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable_approval(db)

    sh = Share(
        created_by_id=creator.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval,
    )
    db.add(sh)
    db.flush()
    from app.models.share_recipient import ShareRecipient
    db.add(ShareRecipient(share_id=sh.id, recipient_user_id=rec.id))
    path = tmp_path / "review.bin"
    path.write_bytes(b"payload")
    f = File(
        id="00000000-0000-0000-0000-00000000rev1", share_id=sh.id,
        original_filename="review.bin", mime_type="application/octet-stream",
        size_bytes=7, storage_path=str(path), state=FileState.clean,
        uploaded_by_id=creator.id,
    )
    db.add(f)
    db.commit()

    token, _ = await login_as("boss@test.local", PW)
    r = await client.get(
        f"/api/files/{f.id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    assert db.query(DownloadLog).filter(DownloadLog.file_id == f.id).count() == 1, (
        "an approver still takes the full bytes with no download_log row"
    )
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_downloaded.value)
        .one()
    )
    assert row.extra.get("review") is True, "the row does not say it was a review"


@pytest.mark.asyncio
async def test_the_pending_download_does_not_touch_the_share_budget(
    db, make_user, client, login_as, tmp_path
):
    """Control: the exemption exists because the share is not live yet, so its
    budget belongs to recipients who have not seen it."""
    make_user(email="boss@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    _enable_approval(db)

    sh = Share(
        created_by_id=creator.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval, download_limit=3, downloads_remaining=3,
    )
    db.add(sh)
    db.flush()
    path = tmp_path / "budget.bin"
    path.write_bytes(b"payload")
    f = File(
        id="00000000-0000-0000-0000-00000000rev2", share_id=sh.id,
        original_filename="budget.bin", mime_type="application/octet-stream",
        size_bytes=7, storage_path=str(path), state=FileState.clean,
        uploaded_by_id=creator.id,
    )
    db.add(f)
    db.commit()

    token, _ = await login_as("boss@test.local", PW)
    r = await client.get(
        f"/api/files/{f.id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    db.refresh(sh)
    assert sh.downloads_remaining == 3, "the review spent a recipient's download"


def test_the_review_does_not_spend_the_recipients_budget():
    """Why the exemption exists: the share is not live yet, so its download
    budget belongs to recipients who have not seen it."""
    from app.routers import files as files_router

    src = inspect.getsource(files_router.download_file)
    dec = src.index("try_decrement_share_counter")
    guard = src.rindex("share.state == ShareState.active", 0, dec)
    assert guard > 0, "the budget decrement is no longer gated on an active share"


# --- flow-approval-5 ---------------------------------------------------------


def test_an_admin_can_still_decide_after_the_feature_is_switched_off(db, make_user):
    from app.services import share_approval as approval_svc

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    creator = make_user(email="emp@test.local", role=UserRole.employee)
    stranded = Share(
        created_by_id=creator.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval,
    )
    db.add(stranded)
    db.commit()

    assert approval_svc.is_enabled(db) is False
    assert approval_svc.can_decide(db, admin, stranded) is True


def test_a_non_admin_still_cannot_approve_with_the_feature_off(db, make_user):
    """Control: the escape hatch is for admins, not a way to re-enable the
    feature by the back door."""
    from app.services import share_approval as approval_svc

    emp = make_user(email="emp@test.local", role=UserRole.employee)
    other = make_user(email="emp2@test.local", role=UserRole.employee)
    stranded = Share(
        created_by_id=other.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval,
    )
    db.add(stranded)
    db.commit()
    assert approval_svc.can_decide(db, emp, stranded) is False


def test_self_approval_is_still_refused(db, make_user):
    """The one rule that must survive every change to this gate."""
    from app.services import share_approval as approval_svc

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    mine = Share(
        created_by_id=admin.id, kind=ShareKind.outbound,
        state=ShareState.pending_approval,
    )
    db.add(mine)
    db.commit()
    assert approval_svc.can_decide(db, admin, mine) is False


# --- flow-onboarding-7 -------------------------------------------------------


def test_registration_goes_through_the_login_funnel():
    from app.routers import auth as auth_router

    src = inspect.getsource(auth_router.register_from_invite)
    assert "finalize_successful_login" in src, (
        "the session is still minted inline, so known_devices is never seeded"
    )
    assert "notify_new_device=False" in src


class _FakeClient:
    host = "203.0.113.9"


class _FakeRequest:
    """Enough of a Request for the device fingerprint: a UA and a client IP."""

    client = _FakeClient()
    headers = {"user-agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"}
    url = type("U", (), {"path": "/api/auth/register-from-invite"})()
    method = "POST"
    state = type("S", (), {})()


def test_the_funnel_can_seed_a_device_without_alerting(db, make_user, monkeypatch):
    from app.config import settings
    from app.models.known_device import KnownDevice
    from app.services import auth as auth_svc
    from app.services import login_alert as la

    fired = []
    monkeypatch.setattr(
        la, "fire_new_device_alert", lambda *a, **kw: fired.append(kw)
    )

    user = make_user(email="new@test.local", role=UserRole.client)
    auth_svc.finalize_successful_login(
        db, user=user, request=_FakeRequest(), settings=settings,
        via="register_from_invite", email_value=user.email,
        notify_new_device=False,
    )
    db.commit()

    assert db.query(KnownDevice).filter(KnownDevice.user_id == user.id).count() == 1, (
        "the device was not seeded, so the next sign-in still looks new"
    )
    assert fired == [], "registration alerted the user about their own signup"


def test_an_ordinary_login_still_alerts(db, make_user, monkeypatch):
    """Control: suppressing the alert for one flow must not disable it."""
    from app.config import settings
    from app.services import auth as auth_svc
    from app.services import login_alert as la

    fired = []
    monkeypatch.setattr(
        la, "fire_new_device_alert", lambda *a, **kw: fired.append(kw)
    )

    user = make_user(email="regular@test.local", role=UserRole.client)
    auth_svc.finalize_successful_login(
        db, user=user, request=_FakeRequest(), settings=settings,
        via="password", email_value=user.email,
    )
    db.commit()
    assert len(fired) == 1
