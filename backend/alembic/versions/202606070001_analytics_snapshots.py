"""analytics_snapshots - daily point-in-time storage/file-state series for the
admin analytics dashboard's storage-growth trend.

Revision ID: 202606070001
Revises: 202606060002
Create Date: 2026-06-07
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202606070001"
down_revision = "202606060002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "analytics_snapshots"):
        op.create_table(
            "analytics_snapshots",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("snapshot_date", sa.Date(), nullable=False),
            sa.Column("storage_bytes", sa.BigInteger(), nullable=False),
            sa.Column("files_clean", sa.Integer(), nullable=False),
            sa.Column("files_infected", sa.Integer(), nullable=False),
            sa.Column("files_total", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    # Indexes are created OUTSIDE the table guard, each guarded on its own name.
    # Nested inside it, a crash between create_table and create_index left the
    # index missing forever: the rerun saw the table and skipped the whole block
    # (audit 2026-07-30).
    # This one is UNIQUE - the whole idempotency of snapshot_storage_today
    # rests on it - so losing it silently is the worst case of the family.
    if not _has_index(bind, "analytics_snapshots", "ix_analytics_snapshots_snapshot_date"):
        op.create_index(
            "ix_analytics_snapshots_snapshot_date",
            "analytics_snapshots",
            ["snapshot_date"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "analytics_snapshots"):
        return
    op.drop_index("ix_analytics_snapshots_snapshot_date", table_name="analytics_snapshots")
    op.drop_table("analytics_snapshots")
