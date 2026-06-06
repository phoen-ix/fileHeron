"""email_log - append-only outbound email log (v1.11.0).

One row per email across all send paths; `status` walks queued →
sent/failed/error and is UPDATEd in place. Bodies stored with one-time
auth-link tokens masked at rest.

Note: the BigInteger PK is created as plain BigInteger here (like
`audit_log`). On SQLite that does not autoincrement, but the test suite
builds the schema via `Base.metadata.create_all` (which uses the model's
INTEGER variant), not this migration - so this runs against MySQL in
practice. Guards make it re-runnable after a partial failure.

Revision ID: 202606051500
Revises: 202606051400
Create Date: 2026-06-05
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "202606051500"
down_revision = "202606051400"
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


_BODY = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")

_INDEXES = [
    ("ix_email_log_created_at", ["created_at"]),
    ("ix_email_log_recipient_email", ["recipient_email"]),
    ("ix_email_log_recipient_user_id", ["recipient_user_id"]),
    ("ix_email_log_category", ["category"]),
    ("ix_email_log_status", ["status"]),
    ("ix_email_log_masked", ["masked"]),
    ("ix_email_log_recipient_created", ["recipient_user_id", "created_at"]),
    ("ix_email_log_status_created", ["status", "created_at"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "email_log"):
        op.create_table(
            "email_log",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("recipient_email", sa.String(320), nullable=False),
            sa.Column("recipient_user_id", sa.Integer(), nullable=True),
            sa.Column("category", sa.String(48), nullable=True),
            sa.Column("template_slug", sa.String(64), nullable=True),
            sa.Column("via", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
            sa.Column("subject", sa.String(512), nullable=False),
            sa.Column("body_text", _BODY, nullable=True),
            sa.Column("body_html", _BODY, nullable=True),
            sa.Column("masked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("smtp_code", sa.Integer(), nullable=True),
            sa.Column("error_class", sa.String(64), nullable=True),
            sa.Column("error_message", sa.String(500), nullable=True),
            sa.Column("job_id", sa.String(64), nullable=True),
            sa.Column("source_log_id", sa.BigInteger(), nullable=True),
            sa.ForeignKeyConstraint(
                ["recipient_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["source_log_id"], ["email_log.id"], ondelete="SET NULL"
            ),
        )
    for name, cols in _INDEXES:
        if not _has_index(bind, "email_log", name):
            op.create_index(name, "email_log", cols)


def downgrade() -> None:
    bind = op.get_bind()
    for name, _cols in _INDEXES:
        if _has_index(bind, "email_log", name):
            op.drop_index(name, table_name="email_log")
    if _has_table(bind, "email_log"):
        op.drop_table("email_log")
