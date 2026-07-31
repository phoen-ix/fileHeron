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
from app.db_guards import _has_column, _has_index

revision = "202606160001"
down_revision = "202606150001"
branch_labels = None
depends_on = None


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
