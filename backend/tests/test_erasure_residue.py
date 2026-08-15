"""What survived a GDPR erasure that reported itself complete.

`erase_user` anonymises the users row rather than deleting it, so no FK CASCADE
fires and every table keyed to that person has to be handled by hand. Eight
findings from the 2026-07-30 audit are the tables that were missed, plus one
concurrency bug and one honesty bug in the receipt:

  admin-7 / flow-erasure-8  error_log kept the real client IP and the user id,
                            and was absent from pii_purged entirely - not a
                            documented retention decision, an omission.
  admin-8 / flow-erasure-6  group_members survived, and because
                            `recompute_shared_group_connections_for_user`
                            derives connections FROM memberships, the next
                            co-member change recreated the connection rows
                            erasure had just deleted. Self-undoing.
  flow-erasure-3            inbound attachment bytes, filenames and admin
                            download routes survived the sender scrub.
  flow-erasure-4            the retained `files` marker row kept
                            original_filename, and the admin browser drops the
                            state filter under include_inactive=true.
  flow-erasure-5            an in-flight tus upload was counted as
                            hard-deleted while its bytes sat in tusd's working
                            directory for hours.
  flow-erasure-7            login_attempts/invites were purged by the CURRENT
                            email only, so rows under a previous address stayed.
  flow-erasure-9            `_is_erased` was an unsynchronised read, so a
                            double-submit produced two receipts and a double
                            quota release.

These tests use `fk_db`, not `db`: the default fixture leaves SQLite foreign
keys OFF, so cascade behaviour cannot be observed there at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.middleware.errors import AppError
from app.models.error_log import ErrorLog
from app.models.file import File, FileState
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.login_attempt import LoginAttempt
from app.models.user import UserRole
from app.services import erasure
from app.utils.timeutil import utc_now


@pytest.fixture
def victim(db, make_user):
    return make_user(email="gone@test.local", role=UserRole.client)


@pytest.fixture
def victim_share(db, victim):
    """files.share_id is NOT NULL, so every file needs a parent share."""
    from app.models.share import Share, ShareKind, ShareState

    sh = Share(created_by_id=victim.id, kind=ShareKind.inbound, state=ShareState.active)
    db.add(sh)
    db.commit()
    return sh


@pytest.fixture
def admin(db, make_user):
    return make_user(email="admin@test.local", role=UserRole.admin)


# --- admin-7 / flow-erasure-8: error_log ------------------------------------


def test_error_log_ip_and_user_id_are_scrubbed(db, admin, victim):
    db.add(
        ErrorLog(
            source="http", status_code=500, code="INTERNAL_ERROR", method="GET",
            path="/api/shares", user_id=victim.id, ip="203.0.113.44",
            signature="sig-a", created_at=utc_now(),
        )
    )
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    row = db.query(ErrorLog).one()
    assert row.ip is None, "the erased user's real client IP survived"
    assert row.user_id is None
    assert result["pii_purged"]["error_log_scrubbed"] == 1


def test_error_log_rows_are_kept_not_deleted(db, admin, victim):
    """Control: the row is scan-triage data. Scrub the person, keep the count."""
    db.add(
        ErrorLog(
            source="http", status_code=404, code="NOT_FOUND", method="GET",
            path="/.env", user_id=victim.id, ip="203.0.113.44", signature="sig-b",
            created_at=utc_now(),
        )
    )
    db.commit()
    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    assert db.query(ErrorLog).count() == 1


# --- admin-8 / flow-erasure-6: group membership -----------------------------


def test_group_memberships_are_deleted(db, admin, victim):
    g = Group(name="Acme", name_normalized="acme", created_by_id=admin.id)
    db.add(g)
    db.commit()
    db.add(GroupMember(group_id=g.id, user_id=victim.id))
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    assert db.query(GroupMember).filter(GroupMember.user_id == victim.id).count() == 0, (
        "membership survived, so the next recompute resurrects the connections"
    )
    assert result["pii_purged"]["group_members"] == 1


def test_other_members_are_untouched(db, admin, victim, make_user):
    """Control: erasing one member must not empty the group."""
    other = make_user(email="stays@test.local", role=UserRole.employee)
    g = Group(name="Acme", name_normalized="acme", created_by_id=admin.id)
    db.add(g)
    db.commit()
    db.add_all([GroupMember(group_id=g.id, user_id=victim.id),
                GroupMember(group_id=g.id, user_id=other.id)])
    db.commit()

    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    assert db.query(GroupMember).filter(GroupMember.user_id == other.id).count() == 1


# --- flow-erasure-7: addresses the person used to have ----------------------


def test_login_attempts_under_a_previous_address_are_purged(db, admin, victim):
    """The old address is not stored on the change token - it only survives in
    the `email_changed` audit row, which is exactly why that is where
    erase_user reads it from."""
    from app.models.audit_log import AuditEventType
    from app.services.audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.email_changed,
        actor_user_id=admin.id,
        target_type="user",
        target_id=str(victim.id),
        metadata={"old_email": "old@test.local", "new_email": "gone@test.local"},
    )
    db.add_all([
        LoginAttempt(email="old@test.local", ip="1.2.3.4", outcome="invalid_credentials",
                     attempted_at=utc_now()),
        LoginAttempt(email="gone@test.local", ip="1.2.3.4", outcome="success",
                     attempted_at=utc_now()),
    ])
    db.commit()

    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    assert db.query(LoginAttempt).count() == 0, (
        "rows under a previous address survived an erasure that claimed to be complete"
    )


def test_a_stranger_with_a_similar_history_is_untouched(db, admin, victim):
    """Control: over-broad matching would delete someone else's forensics."""
    db.add(LoginAttempt(email="other@test.local", ip="1.2.3.4",
                        outcome="invalid_credentials", attempted_at=utc_now()))
    db.commit()
    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    assert db.query(LoginAttempt).count() == 1


# --- flow-erasure-4 / -5: the files the receipt talks about -----------------


def test_the_retained_marker_row_loses_the_filename(db, admin, victim, victim_share, tmp_path):
    p = tmp_path / "payslip.pdf"
    p.write_bytes(b"x" * 8)
    db.add(
        File(
            id="00000000-0000-0000-0000-0000000000e1",
            share_id=victim_share.id,
            original_filename="2026-payslip.pdf", mime_type="application/pdf",
            size_bytes=8, storage_path=str(p), state=FileState.clean,
            uploaded_by_id=victim.id,
        )
    )
    db.commit()

    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    row = db.query(File).one()
    assert row.state == FileState.deleted
    assert row.original_filename == "[erased]", (
        "the admin file browser can still list this person's filenames"
    )


def test_an_in_flight_tus_upload_has_its_partial_unlinked(
    db, admin, victim, victim_share, monkeypatch, tmp_path
):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "TUS_UPLOAD_DIR", str(tmp_path))
    partial = tmp_path / "abc123"
    info = tmp_path / "abc123.info"
    partial.write_bytes(b"half an upload")
    info.write_text("{}")

    db.add(
        File(
            id="00000000-0000-0000-0000-0000000000e2",
            share_id=victim_share.id,
            original_filename="big.bin", mime_type="application/octet-stream",
            size_bytes=999, storage_path=None, state=FileState.uploading,
            uploaded_by_id=victim.id, tus_upload_id="abc123",
        )
    )
    db.commit()

    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    assert not partial.exists(), "tus partial bytes survived the erasure that counted them"
    assert not Path(info).exists()


# --- flow-erasure-9: concurrency --------------------------------------------


def test_a_second_erasure_is_refused(db, admin, victim):
    erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    with pytest.raises(AppError) as exc:
        erasure.erase_user(db, actor=admin, target=victim)
    assert exc.value.code == "ALREADY_ERASED"


def test_erase_takes_a_row_lock_before_the_already_erased_check():
    """The check was an unsynchronised read, so two concurrent requests both
    passed it. SQLite cannot exhibit the race, so this asserts the ordering
    that removes it."""
    import inspect

    src = inspect.getsource(erasure.erase_user)
    lock_at = src.index("with_for_update")
    check_at = src.index("_is_erased(target)")
    assert lock_at < check_at, "the already-erased check still runs before the lock"


# --- the receipt ------------------------------------------------------------


def test_pii_purged_accounts_for_every_new_purge(db, admin, victim):
    """The receipt is what an admin hands a regulator; a purge missing from it
    is indistinguishable from a purge that did not happen."""
    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    for key in ("error_log_scrubbed", "group_members", "inbound_attachments"):
        assert key in result["pii_purged"], f"{key} is not accounted for in the receipt"


# --- filenames on rows that were already `deleted` ---------------------------


def test_erasure_scrubs_filenames_on_already_deleted_rows(db, make_user):
    """By the time an Art.17 request arrives, most of a subject's files are
    already `deleted` - every expiry sweep marks them so and RETAINS
    original_filename, and nothing prunes those rows. The erasure loop skipped
    exactly those, so the subject's real filenames stayed listable through the
    admin file browser, which drops the state filter under include_inactive."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.services import erasure

    admin = make_user(email="adm@test.local", role=UserRole.admin)
    subject = make_user(email="subj@test.local", role=UserRole.client)
    share = Share(created_by_id=subject.id, kind=ShareKind.outbound, subject="s",
                  expires_at=None, state=ShareState.active)
    db.add(share)
    db.flush()
    db.add(File(
        share_id=share.id, original_filename="severance-agreement.pdf",
        mime_type="application/pdf", size_bytes=10,
        state=FileState.deleted, uploaded_by_id=subject.id,
    ))
    db.commit()

    erasure.erase_user(db, target=subject, actor=admin, request=None)

    names = [f.original_filename for f in db.query(File).all()]
    assert "severance-agreement.pdf" not in names, names
    assert names == ["[erased]"]


def test_erasure_scrubs_filenames_out_of_the_audit_log(db, make_user):
    """Every tus upload writes file_finalized carrying {"filename": ...} with
    the uploader as actor. The existing audit scrub matches values that ARE
    e-mail addresses, and a filename never is - so the names survived it and
    stayed recoverable from /api/admin/audit-log?actor_user_id=<id>."""
    from app.models.audit_log import AuditEventType, AuditLog
    from app.models.user import UserRole
    from app.services import erasure

    admin = make_user(email="adm2@test.local", role=UserRole.admin)
    subject = make_user(email="subj2@test.local", role=UserRole.client)
    db.add(AuditLog(
        event_type=AuditEventType.file_finalized.value,
        actor_user_id=subject.id, target_type="file", target_id="f-1",
        extra={"size_bytes": 10, "filename": "medical-report.pdf"},
    ))
    db.commit()

    erasure.erase_user(db, target=subject, actor=admin, request=None)

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_finalized.value)
        .all()
    )
    assert rows and all(r.extra["filename"] == "[erased]" for r in rows)
