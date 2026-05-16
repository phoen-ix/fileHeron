"""cron_runs table for operator-facing cron observability.

Revision ID: 202605030001
Revises: 202605031600
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "202605030001"
down_revision = "202605031600"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "cron_runs"):
        return

    op.create_table(
        "cron_runs",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cron_runs_job_name", "cron_runs", ["job_name"], unique=False
    )
    op.create_index(
        "ix_cron_runs_started_at", "cron_runs", ["started_at"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "cron_runs"):
        return
    op.drop_index("ix_cron_runs_started_at", table_name="cron_runs")
    op.drop_index("ix_cron_runs_job_name", table_name="cron_runs")
    op.drop_table("cron_runs")
