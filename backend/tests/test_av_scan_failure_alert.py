"""A scan that produced no verdict is an event, not a log line.

On 2026-11-20 clamd answered `Access denied` for an upload it could not open.
`av_scan_file` wrote one `logger.error` line to stdout and returned normally:
no audit row, no error-log row, no notification. The only escalation was the
sweep reporting itself failed once a file was still unscanned SIX HOURS later
(and before v2.30.0 nothing at all), so an admin learned of it from the sender.
The log line itself was gone with the worker container at the next update.

Now the first failed scan writes a `file_scan_failed` audit row and sends every
admin the `av_scan_failed` ops alert at once - at most one alert an hour while
scans keep failing; every failure keeps its audit row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from arq import Retry

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.notification import Notification, NotificationCategory
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import av_scan as av_scan_svc
from app.services.av_scan import AVUnavailableError, ScanResult
from app.workers import av_scan as worker
from app.workers.av_scan import av_scan_file

DENIED = "/data/files/2026/11/abc.bin: Access denied. ERROR"


def _file(db, sender, recipient, tmp_path, name: str) -> tuple[str, str]:
    """A share with one file waiting for its scan: (file id, share id)."""
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="Plans",
        message=None,
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    on_disk = tmp_path / name
    on_disk.write_bytes(b"hello")
    f = File(
        id=f"file-{name}",
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=5,
        state=FileState.ready_unscanned,
        storage_path=str(on_disk),
        uploaded_by_id=sender.id,
    )
    db.add(f)
    db.commit()
    return f.id, share.id


@pytest.fixture
def people(make_user):
    return {
        "admin": make_user(email="admin@test.local", role=UserRole.admin),
        "second_admin": make_user(email="ops@test.local", role=UserRole.admin),
        "sender": make_user(email="sender@test.local", role=UserRole.employee),
        "client": make_user(email="client@test.local", role=UserRole.client),
    }


@pytest.fixture
def ids(people):
    """The worker commits and closes the session it is handed, which detaches
    every instance: read rows back by id."""
    return {name: user.id for name, user in people.items()}


@pytest.fixture
def scan(db, monkeypatch):
    """Run the worker on the test session with clamd's answer scripted."""
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)

    def answers(outcome):
        def _scan(*_args):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(av_scan_svc, "scan_stored", _scan)

    return answers


def _failures(db) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_scan_failed.value)
        .order_by(AuditLog.id)
        .all()
    )


def _alerts(db, user_id: int) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.category == NotificationCategory.ops_alert,
        )
        .order_by(Notification.id)
        .all()
    )


@pytest.mark.asyncio
async def test_a_scan_clamd_cannot_complete_is_recorded_and_alarmed(
    db, people, ids, scan, tmp_path
):
    file_id, share_id = _file(db, people["sender"], people["client"], tmp_path, "plan.pdf")
    scan(ScanResult(state="error", signature=None, raw=DENIED))

    result = await av_scan_file(None, file_id)

    assert result["state"] == "error"
    assert db.get(File, file_id).state == FileState.ready_unscanned

    (row,) = _failures(db)
    assert row.target_type == "file" and row.target_id == file_id
    assert row.extra["detail"] == DENIED
    assert row.extra["filename"] == "plan.pdf"
    assert row.extra["share_id"] == share_id

    for who in ("admin", "second_admin"):
        (alert,) = _alerts(db, ids[who])
        assert alert.payload_json["reason"] == "av_scan_failed"
        assert "plan.pdf" in alert.payload_json["detail"]
        assert "Access denied" in alert.payload_json["detail"]
        assert alert.link_url == "/admin/audit-log"
    assert _alerts(db, ids["sender"]) == []
    assert _alerts(db, ids["client"]) == []


@pytest.mark.asyncio
async def test_every_failure_is_recorded_but_admins_are_alarmed_once_an_hour(
    db, people, ids, scan, tmp_path
):
    first, _ = _file(db, people["sender"], people["client"], tmp_path, "one.pdf")
    second, _ = _file(db, people["sender"], people["client"], tmp_path, "two.pdf")
    scan(ScanResult(state="error", signature=None, raw=DENIED))

    await av_scan_file(None, first)
    await av_scan_file(None, second)
    await av_scan_file(None, first)  # the hourly sweep's retry, failing again

    assert [r.target_id for r in _failures(db)] == [first, second, first]
    assert len(_alerts(db, ids["admin"])) == 1


@pytest.mark.asyncio
async def test_a_verdict_raises_no_alarm(db, people, ids, scan, tmp_path):
    file_id, _ = _file(db, people["sender"], people["client"], tmp_path, "fine.pdf")
    scan(ScanResult(state="clean", signature=None, raw="OK"))

    assert (await av_scan_file(None, file_id))["state"] == "clean"

    assert _failures(db) == []
    assert _alerts(db, ids["admin"]) == []


@pytest.mark.asyncio
async def test_a_scan_job_that_crashes_is_recorded_and_alarmed_too(
    db, people, ids, scan, tmp_path
):
    file_id, _ = _file(db, people["sender"], people["client"], tmp_path, "crash.pdf")
    scan(OSError("[Errno 5] Input/output error"))

    with pytest.raises(OSError):
        await av_scan_file(None, file_id)

    (row,) = _failures(db)
    assert "Input/output error" in row.extra["detail"]
    (alert,) = _alerts(db, ids["admin"])
    assert alert.payload_json["reason"] == "av_scan_failed"


@pytest.mark.asyncio
async def test_clamd_being_unreachable_is_a_retry_not_a_failed_scan(
    db, people, ids, scan, tmp_path
):
    """The job comes back (arq Retry); ops_check's ping is what reports a clamd
    that stays down, as `av_unhealthy`."""
    file_id, _ = _file(db, people["sender"], people["client"], tmp_path, "later.pdf")
    scan(AVUnavailableError("cannot connect to clamav:3310"))

    with pytest.raises(Retry):
        await av_scan_file({"job_try": 1}, file_id)

    assert _failures(db) == []
    assert _alerts(db, ids["admin"]) == []
