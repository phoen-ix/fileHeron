"""Verify the @track_cron decorator records success + failure runs."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.cron_run import CronRun, CronRunStatus
from app.services.cron_tracker import CRON_FAILED_KEY, track_cron


@pytest.mark.asyncio
async def test_track_cron_records_success(db):
    @track_cron("test_job_success")
    async def _job(_ctx):
        return {"work_done": 42}

    out = await _job({})
    assert out == {"work_done": 42}

    db.expire_all()
    rows = (
        db.query(CronRun)
        .filter(CronRun.job_name == "test_job_success")
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.status == CronRunStatus.success
    assert row.completed_at is not None
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.result_summary == {"work_done": 42}
    assert row.error_msg is None


@pytest.mark.asyncio
async def test_track_cron_records_failure_and_audits(db):
    @track_cron("test_job_failure")
    async def _job(_ctx):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _job({})

    db.expire_all()
    rows = (
        db.query(CronRun)
        .filter(CronRun.job_name == "test_job_failure")
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.status == CronRunStatus.failure
    assert row.completed_at is not None
    assert row.error_msg is not None
    assert "boom" in row.error_msg

    audit_rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.cron_failed.value,
            AuditLog.target_id == "test_job_failure",
        )
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].extra is not None
    assert "boom" in audit_rows[0].extra.get("error", "")


@pytest.mark.asyncio
async def test_an_explicit_failure_signal_records_failure_without_raising(db):
    """A cron that catches its own errors and reports them in the result was
    recorded as a SUCCESS - it could be broken indefinitely behind a green
    Scheduled-tasks page. It can now say so via CRON_FAILED_KEY.

    Not by raising: `max_tries` is 5, so a job failing on a transient upstream
    would be re-run five times per tick and would write five audit rows.
    """
    calls = {"n": 0}

    @track_cron("test_job_reported_failure")
    async def _job(_ctx):
        calls["n"] += 1
        return {"ok": False, "error": "upstream returned 0 releases", CRON_FAILED_KEY: True}

    out = await _job({})  # must NOT raise
    assert out["ok"] is False
    assert calls["n"] == 1

    db.expire_all()
    rows = (
        db.query(CronRun)
        .filter(CronRun.job_name == "test_job_reported_failure")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == CronRunStatus.failure
    assert rows[0].error_msg is not None
    assert "0 releases" in rows[0].error_msg

    audit_rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == AuditEventType.cron_failed.value,
            AuditLog.target_id == "test_job_reported_failure",
        )
        .all()
    )
    assert len(audit_rows) == 1
    assert "0 releases" in audit_rows[0].extra.get("error", "")


@pytest.mark.asyncio
async def test_a_result_that_merely_says_not_ok_is_still_a_success(db):
    """The signal is opt-in. Several crons return `ok` flags meaning something
    else entirely, and none of them should start reporting failures."""
    @track_cron("test_job_not_ok_but_no_signal")
    async def _job(_ctx):
        return {"ok": False, "error": "informational"}

    await _job({})

    db.expire_all()
    rows = (
        db.query(CronRun)
        .filter(CronRun.job_name == "test_job_not_ok_but_no_signal")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == CronRunStatus.success
