"""share_recipients

Revision ID: 202605020913
Revises: 202605020912
Create Date: 2026-05-02 09:13:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020913"
down_revision: str | None = "202605020912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "share_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("share_id", sa.String(36), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        # Becomes a real FK to `groups` in Phase 4.
        sa.Column("recipient_group_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["share_id"], ["shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_share_recipients_share_id", "share_recipients", ["share_id"])
    op.create_index(
        "ix_share_recipients_recipient_user_id", "share_recipients", ["recipient_user_id"]
    )
    op.create_index(
        "ix_share_recipients_recipient_group_id", "share_recipients", ["recipient_group_id"]
    )


def downgrade() -> None:
    # drop_table removes these indexes (incl. the FK-backing share_id /
    # recipient_user_id ones) with the table; dropping a FK-backed index first
    # errors (MySQL 1553).
    op.drop_table("share_recipients")
