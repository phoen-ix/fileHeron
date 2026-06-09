"""error_log.ip - record the client IP on each error row (v1.54.0).

The error log existed since v1.53.0 but never stored the client IP, which is the
key field for spotting / correlating vuln scans. Add it (nullable; old rows stay
NULL) plus an (ip, created_at) index for "show everything from this IP".
Re-runnable via _has_column / _has_index guards.

Revision ID: 202606160001
Revises: 202606150001
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606160001"
down_revision = "202606150001"
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


def _has_index(bind, table: str, index: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND index_name = :i LIMIT 1"
            ),
            {"t": table, "i": index},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:i AND tbl_name=:t"
        ),
        {"i": index, "t": table},
    ).fetchone()
    return rows is not None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "error_log", "ip"):
        op.add_column("error_log", sa.Column("ip", sa.String(45), nullable=True))
    if not _has_index(bind, "error_log", "ix_error_log_ip_created"):
        op.create_index("ix_error_log_ip_created", "error_log", ["ip", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "error_log", "ix_error_log_ip_created"):
        op.drop_index("ix_error_log_ip_created", table_name="error_log")
    if _has_column(bind, "error_log", "ip"):
        op.drop_column("error_log", "ip")
