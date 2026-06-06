"""Add shares.terminated_at — when a share became terminal (revoked/deleted).

Powers the reclaim_orphaned_files cron's grace window: a soft-revoked share
keeps its bytes for ORPHAN_RECLAIM_AFTER_DAYS before the cron frees them.
Backfills existing terminal shares to "now" so they get a full grace window
from deploy (admins can also reclaim immediately from /admin/file-history).

Revision ID: 202605312000
Revises: 202605170200
Create Date: 2026-05-31
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "202605312000"
down_revision = "202605170200"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "shares", "terminated_at"):
        op.add_column("shares", sa.Column("terminated_at", sa.DateTime(), nullable=True))
    # Backfill existing revoked/deleted shares so the grace window starts now
    # (naive-UTC, matching the app convention; portable across MariaDB/SQLite).
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    bind.execute(
        sa.text(
            "UPDATE shares SET terminated_at = :now "
            "WHERE state IN ('revoked', 'deleted') AND terminated_at IS NULL"
        ),
        {"now": now},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "shares", "terminated_at"):
        op.drop_column("shares", "terminated_at")
