"""error_log - append-only server-error log (v1.53.0).

One row per captured error event (HTTP 5xx, opted-in 4xx, failed crons).
Written by the ``notify_admin_error`` worker before the alert saferails, so the
log is complete independent of which errors actually emailed. Guards make it
re-runnable after a partial failure (mirrors ``email_log``); the BigInteger PK is
plain here and only ever runs against MySQL (tests build via create_all).

Revision ID: 202606150001
Revises: 202606140001
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606150001"
down_revision = "202606140001"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t LIMIT 1"
            ),
            {"t": table},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return rows is not None


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


_INDEXES = [
    ("ix_error_log_created_at", ["created_at"]),
    ("ix_error_log_request_id", ["request_id"]),
    ("ix_error_log_code_created", ["code", "created_at"]),
    ("ix_error_log_signature", ["signature"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "error_log"):
        op.create_table(
            "error_log",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("exception_type", sa.String(128), nullable=True),
            sa.Column("message", sa.String(500), nullable=True),
            sa.Column("method", sa.String(8), nullable=True),
            sa.Column("path", sa.String(512), nullable=True),
            sa.Column("job_name", sa.String(128), nullable=True),
            sa.Column("request_id", sa.String(64), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("auth_via", sa.String(16), nullable=True),
            sa.Column("signature", sa.String(16), nullable=False),
            sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    for name, cols in _INDEXES:
        if not _has_index(bind, "error_log", name):
            op.create_index(name, "error_log", cols)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "error_log"):
        op.drop_table("error_log")
