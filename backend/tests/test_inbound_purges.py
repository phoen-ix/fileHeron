"""The two inbound-mail purges leave a durable trace when a blob will not unlink.

`routers/admin/imap.py::delete_inbox_message` and
`workers/prune_history.py::_prune_inbound` both delete rows whose attachment
bytes nothing else references. No sweeper walks inbound locators, so a blob
that fails to unlink after the rows are gone is orphaned for good - and until
2026-11-08 the route said so only in a `logger.warning` (stdout rotates; it
reaches neither error_log nor an alert) and the worker only in its own result
dict, while also unlinking BEFORE deleting the rows, the inverse of the order
every other deferred purge keeps. Both now go through `file.purge_locators`,
which writes a `file_purge_failed` audit row per failure - the same record
`purge_expired_bytes` has left since v2.5.0.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass, MessageStatus
from app.models.user import UserRole
from app.services import file as file_svc
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


class _Backend:
    """Records deletes; refuses the locators in `broken`."""

    def __init__(self, broken=()):
        self.deleted: list[str] = []
        self.broken = set(broken)

    def delete(self, locator):
        if locator in self.broken:
            raise OSError("EIO")
        self.deleted.append(locator)


_UIDS = iter(range(1, 1000))


def _message_with_attachments(db, keys, *, age_days=0):
    m = InboundMessage(
        sender_email="x@example.com", subject="Hello", imap_uid=next(_UIDS), uidvalidity=1,
        classification=MessageClass.normal, status=MessageStatus.new,
        has_attachments=True, created_at=utc_now() - timedelta(days=age_days),
    )
    db.add(m)
    db.flush()
    for i, key in enumerate(keys):
        db.add(InboundAttachment(
            message_id=m.id, filename=f"f{i}.pdf", size_bytes=3, storage_key=key,
            av_state=AttachmentAVState.clean,
        ))
    db.commit()
    return m


def _purge_failures(db) -> list[str]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_purge_failed.value)
        .order_by(AuditLog.id)
        .all()
    )
    return [row.extra["locator"] for row in rows]


@pytest.mark.asyncio
async def test_deleting_an_inbox_message_records_a_blob_that_would_not_unlink(
    make_user, db, client, login_as, monkeypatch
):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    m = _message_with_attachments(db, ["inbound/a.bin", "inbound/b.bin"])
    backend = _Backend(broken={"inbound/b.bin"})
    monkeypatch.setattr(file_svc, "get_storage_backend", lambda: backend)

    r = await client.delete(f"/api/admin/inbox/{m.id}", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 204, r.text
    assert backend.deleted == ["inbound/a.bin"], "the unlinkable blob was removed"
    assert db.query(InboundMessage).count() == 0, "the delete itself went through"
    rows = _purge_failures(db)
    assert rows == ["inbound/b.bin"], "the failed unlink left no durable record"
    failed = db.query(AuditLog).filter(AuditLog.event_type == AuditEventType.file_purge_failed.value).one()
    assert failed.extra["reason"] == "inbound_message_deleted"


@pytest.mark.asyncio
async def test_prune_inbound_deletes_the_rows_first_and_records_a_failed_unlink(db, monkeypatch):
    from app.workers import prune_history

    _message_with_attachments(db, ["inbound/old-a.bin", "inbound/old-b.bin"], age_days=400)
    _message_with_attachments(db, ["inbound/fresh.bin"])
    order: list[str] = []
    backend = _Backend(broken={"inbound/old-b.bin"})
    real_delete = backend.delete

    def _delete(locator):
        # By the time a blob is unlinked, its rows must already be gone - the
        # order every deferred purge in the codebase keeps.
        order.append(f"rows={db.query(InboundMessage).count()} unlink={locator}")
        real_delete(locator)

    backend.delete = _delete
    monkeypatch.setattr(file_svc, "get_storage_backend", lambda: backend)

    pruned, undeleted = await prune_history._prune_inbound(365)

    assert pruned == 1
    assert undeleted == ["inbound/old-b.bin"]
    assert backend.deleted == ["inbound/old-a.bin"]
    assert order[0].startswith("rows=1 "), order  # the old message was already deleted
    assert _purge_failures(db) == ["inbound/old-b.bin"]
    assert db.query(InboundAttachment).filter_by(storage_key="inbound/fresh.bin").count() == 1
