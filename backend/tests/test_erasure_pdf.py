"""Erasure preflight summary + PDF receipt generation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import erasure as erasure_svc


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_preflight_summary_counts_files_and_shares(
    make_user, db, tmp_path
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="t@test.local", role=UserRole.client)

    share = Share(
        created_by_id=target.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=_now() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    on_disk = tmp_path / "f.bin"
    on_disk.write_bytes(b"hello")
    db.add(
        File(
            id="erase-test-1",
            share_id=share.id,
            original_filename="x.bin",
            mime_type="application/octet-stream",
            size_bytes=5,
            state=FileState.ready_unscanned,
            storage_path=str(on_disk),
            uploaded_by_id=target.id,
        )
    )
    # A share where target is a recipient
    other_share = Share(
        created_by_id=admin.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=_now() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(other_share)
    db.flush()
    db.add(ShareRecipient(share_id=other_share.id, recipient_user_id=target.id))
    db.commit()

    summary = erasure_svc.compute_erasure_summary(db, target=target)
    assert summary["files_to_delete"] == 1
    assert summary["bytes_to_delete"] == 5
    assert summary["shares_created"] == 1
    assert summary["shares_received_to_anonymize"] == 1
    assert summary["is_already_erased"] is False


def test_pdf_receipt_renders(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="t@test.local", role=UserRole.client)
    row = AuditLog(
        actor_user_id=admin.id,
        event_type=AuditEventType.user_erased.value,
        target_type="user",
        target_id=str(target.id),
        request_id="req-test-12345",
        ip="127.0.0.1",
        extra={"deleted_files": 7, "deleted_bytes": 12345},
    )
    db.add(row)
    db.commit()

    pdf = erasure_svc.generate_receipt_pdf(row)
    # PDF magic header.
    assert pdf[:4] == b"%PDF"
    # Sanity — file is non-trivial size.
    assert len(pdf) > 1000


@pytest.mark.asyncio
async def test_already_erased_user_summary_flag(make_user, db):
    target = make_user(email="t@test.local", role=UserRole.client)
    target.email = "[erased]"
    target.display_name = "[erased]"
    db.commit()

    summary = erasure_svc.compute_erasure_summary(db, target=target)
    assert summary["is_already_erased"] is True
