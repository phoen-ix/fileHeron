"""shares

Revision ID: 202605020911
Revises: 202605020910
Create Date: 2026-05-02 09:11:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020911"
down_revision: str | None = "202605020910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_shares_created_by_id", "shares", ["created_by_id"])
    op.create_index("ix_shares_expires_at", "shares", ["expires_at"])
    op.create_index("ix_shares_state", "shares", ["state"])


def downgrade() -> None:
    op.drop_index("ix_shares_state", table_name="shares")
    op.drop_index("ix_shares_expires_at", table_name="shares")
    op.drop_index("ix_shares_created_by_id", table_name="shares")
    op.drop_table("shares")
