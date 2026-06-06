"""Daily point-in-time storage snapshot for the admin analytics dashboard.

Most analytics are reconstructed live from persisted timestamps (share/download/
audit `created_at`), so they need no snapshot. The ONE thing that can't be
reconstructed is *storage / file-state over time* — once a file is hard-deleted
its bytes are gone and the past can't be recomputed. So we keep a tiny org-level
row per day (written by the `analytics_aggregate` cron) purely for the
storage-growth trend; everything else the endpoint computes on the fly.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One row per calendar day (UTC). Unique so the cron can upsert today's row.
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    files_clean: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_infected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
