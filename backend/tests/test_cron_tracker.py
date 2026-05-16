"""Verify the @track_cron decorator records success + failure runs."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.cron_run import CronRun, CronRunStatus
from app.services.cron_tracker import track_cron


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
