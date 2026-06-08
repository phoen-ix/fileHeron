"""download_log

Revision ID: 202605020915
Revises: 202605020914
Create Date: 2026-05-02 09:15:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020915"
down_revision: str | None = "202605020914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("file_id", sa.String(36), nullable=False),
        sa.Column("share_id", sa.String(36), nullable=False),
        sa.Column("accessed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("ua_fingerprint_hash", sa.String(64), nullable=True),
        sa.Column("bytes_served", sa.BigInteger(), nullable=True),
        sa.Column("via", sa.String(12), nullable=False, server_default="auth"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_id"], ["shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accessed_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_download_log_file_id", "download_log", ["file_id"])
    op.create_index("ix_download_log_share_id", "download_log", ["share_id"])
    op.create_index("ix_download_log_accessed_by_user_id", "download_log", ["accessed_by_user_id"])
    op.create_index("ix_download_log_accessed_at", "download_log", ["accessed_at"])


def downgrade() -> None:
    # drop_table removes these indexes (incl. the FK-backing file_id / share_id /
    # accessed_by_user_id ones) with the table; dropping a FK-backed index first
    # errors (MySQL 1553).
    op.drop_table("download_log")
