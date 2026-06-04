"""Per-cron-execution book-keeping for operator visibility.

ARQ logs cron firings to stdout. That works for forensic grepping but
not for the admin shell answering "did quota_reconcile run today?"
without SSHing the host. Every cron wraps its body in
`services/cron_tracker.py::track_run(...)` which writes a row here at
start and updates it on completion (success or failure).

Retention: the tracker prunes rows older than 30 days at the end of
each run, capped at last 200 per job_name. Operator dashboards only
need recency + failure-rate-this-week; the audit log is the durable
forensic record.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now


class CronRunStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failure = "failure"


# BIGINT does not autoincrement under SQLite; fall through to INTEGER (ROWID).
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")




class CronRun(Base):
    __tablename__ = "cron_runs"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    status: Mapped[CronRunStatus] = mapped_column(
        SAEnum(CronRunStatus, native_enum=False, length=20),
        nullable=False,
        default=CronRunStatus.running,
    )
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
