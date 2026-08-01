"""Two audit #2 findings where a comment described a guarantee the code did not
provide.

`erasure.erase_user` scrubbed plaintext addresses out of `audit_log` by
ENUMERATING two event types. Three other event types write the same person's
address at their own call sites - `invite_created`, `invite_revoked`,
`email_undeliverable` - so an erased subject's real address stayed in
/admin/audit and its CSV export for `AUDIT_LOG_RETENTION_DAYS` (365), after an
erasure that had just issued a signed receipt saying the record referenced them
"by anonymised id" only. An enumeration cannot stay correct: nothing ties it to
the call sites, and every event added later starts out missing from it.

`file.finalize_to_disk` wrote the intended locator before consuming the tusd
working file, with a comment explaining that this is what makes a commit failure
recoverable - and then only FLUSHED it. A flush is invisible outside the
transaction, so a rollback took the locator with it and left the bytes exactly
as orphaned as before, with the only other way to find them (the tusd working
file) already consumed by the move.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole

VICTIM = "client@realname.example"


def _audit(db, *, event, target_id, extra):
    db.add(
        AuditLog(
            event_type=event.value if hasattr(event, "value") else event,
            target_type="user",
            target_id=target_id,
            extra=extra,
        )
    )
    db.flush()


def test_erasure_leaves_no_plaintext_address_in_any_audit_row(db, make_user):
    """The three event types the enumeration missed, written exactly as their
    production call sites write them."""
    from app.services import erasure

    victim = make_user(email=VICTIM, role=UserRole.client)
    admin = make_user(email="admin@test.local", role=UserRole.admin)

    _audit(
        db,
        event=AuditEventType.invite_created,
        target_id=str(victim.id),
        extra={"email": VICTIM, "target_role": "client"},
    )
    _audit(
        db,
        event=AuditEventType.invite_revoked,
        target_id=str(victim.id),
        extra={"email": VICTIM},
    )
    _audit(
        db,
        event=AuditEventType.email_undeliverable,
        target_id=VICTIM,
        extra={"to": VICTIM, "reason": "550 unknown user"},
    )
    db.commit()

    erasure.erase_user(db, target=victim, actor=admin)
    db.commit()

    leaks = [
        (r.event_type, r.target_id, r.extra)
        for r in db.query(AuditLog).all()
        if VICTIM in (r.target_id or "") or VICTIM in str(r.extra or {})
    ]
    assert leaks == [], f"an erased subject's address survives in audit_log: {leaks}"


def test_the_events_themselves_are_kept(db, make_user):
    """The control. audit_log is the append-only record the erasure receipt
    verifies against - scrubbing must not become deleting."""
    from app.services import erasure

    victim = make_user(email="keep@realname.example", role=UserRole.client)
    admin = make_user(email="admin2@test.local", role=UserRole.admin)
    _audit(
        db,
        event=AuditEventType.invite_created,
        target_id=str(victim.id),
        extra={"email": "keep@realname.example", "target_role": "client"},
    )
    db.commit()
    before = db.query(AuditLog).count()

    erasure.erase_user(db, target=victim, actor=admin)
    db.commit()

    rows = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.invite_created.value
    ).all()
    assert len(rows) == 1
    assert rows[0].extra["target_role"] == "client", "non-PII context must survive"
    assert rows[0].extra["email"] == "[erased]"
    assert db.query(AuditLog).count() >= before


def test_an_unrelated_persons_address_is_untouched(db, make_user):
    """An over-broad scrub would erase the record of everyone else."""
    from app.services import erasure

    victim = make_user(email="v3@realname.example", role=UserRole.client)
    other = make_user(email="bystander@corp.example", role=UserRole.employee)
    admin = make_user(email="admin3@test.local", role=UserRole.admin)
    _audit(
        db,
        event=AuditEventType.invite_created,
        target_id=str(other.id),
        extra={"email": "bystander@corp.example"},
    )
    db.commit()

    erasure.erase_user(db, target=victim, actor=admin)
    db.commit()

    row = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.invite_created.value
    ).one()
    assert row.extra["email"] == "bystander@corp.example"


# --- finalize durability ----------------------------------------------------


def test_the_locator_survives_a_failed_commit(db, make_user, tmp_path, monkeypatch):
    """The bytes have already moved when the commit fails. If the locator went
    with the rollback, nothing on the instance could ever find them again: the
    tusd sweeper looks for working files (consumed), and every `files`-row
    sweeper reads `storage_path` (NULL)."""
    from app.config import settings
    from app.services import file as file_svc

    monkeypatch.setattr(settings, "TUS_UPLOAD_DIR", str(tmp_path / "tus"))
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path / "files"))
    Path(settings.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.STORAGE_ROOT).mkdir(parents=True, exist_ok=True)
    src = Path(settings.TUS_UPLOAD_DIR) / "tusid123"
    src.write_bytes(b"payload" * 100)

    owner = make_user(email="up@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    row = File(
        id="00000000-0000-0000-0000-00000000fin1",
        share_id=sh.id,
        original_filename="doc.bin",
        mime_type="application/octet-stream",
        size_bytes=700,
        state=FileState.uploading,
        uploaded_by_id=owner.id,
    )
    db.add(row)
    db.commit()

    file_svc.finalize_to_disk(db, file=row, tus_upload_id="tusid123")
    db.rollback()  # the commit that would follow, failing

    db.expire_all()
    fresh = db.query(File).filter(File.id == row.id).one()
    assert fresh.storage_path, (
        "the locator was only flushed, so the rollback discarded it and the "
        "bytes on disk are unreachable and charged to nobody"
    )
    assert Path(fresh.storage_path).is_file()
    assert not src.exists(), "the control: the tusd working file really is gone"


@pytest.mark.asyncio
async def test_the_stale_upload_sweeper_can_act_on_that_row(db, make_user, tmp_path):
    """The recovery the durable locator exists to enable, end to end."""
    from datetime import timedelta

    from app.utils.timeutil import utc_now
    from app.workers import cleanup_stale_uploads as sweeper

    owner = make_user(email="up2@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    blob = tmp_path / "orphan.bin"
    blob.write_bytes(b"x" * 50)
    db.add(
        File(
            id="00000000-0000-0000-0000-00000000fin2",
            share_id=sh.id,
            original_filename="orphan.bin",
            mime_type="application/octet-stream",
            size_bytes=50,
            storage_path=str(blob),
            state=FileState.uploading,
            created_at=utc_now() - timedelta(days=3),
            uploaded_by_id=owner.id,
        )
    )
    db.commit()

    await sweeper.cleanup_stale_uploads(None)

    assert not blob.exists(), "the sweeper could not reclaim the orphaned bytes"
