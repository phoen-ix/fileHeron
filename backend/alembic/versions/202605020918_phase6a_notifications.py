"""phase6a - notifications + user_notification_preferences + shares.expiring_notified_at

Revision ID: 202605020918
Revises: 202605020917
Create Date: 2026-05-02 09:18:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020918"
down_revision: str | None = "202605020917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("link_url", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_category", "notifications", ["category"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    op.create_table(
        "user_notification_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "category"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.add_column(
        "shares",
        sa.Column("expiring_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shares", "expiring_notified_at")
    op.drop_table("user_notification_preferences")
    # drop_table removes these indexes (incl. the FK-backing user_id one) with
    # the table; dropping a FK-backed index first errors (MySQL 1553).
    op.drop_table("notifications")
