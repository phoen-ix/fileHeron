"""webhooks + webhook_deliveries — outbound signed webhooks (v1.19.0).

Revision ID: 202606080001
Revises: 202606070001
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606080001"
down_revision = "202606070001"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "webhooks"):
        op.create_table(
            "webhooks",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("url", sa.String(2048), nullable=False),
            sa.Column("secret_encrypted", sa.Text(), nullable=False),
            sa.Column("event_types", sa.JSON(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_table(bind, "webhook_deliveries"):
        op.create_table(
            "webhook_deliveries",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False, autoincrement=True),
            sa.Column("webhook_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("response_code", sa.Integer(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("error", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
        op.create_index("ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "webhook_deliveries"):
        op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
        op.drop_index("ix_webhook_deliveries_webhook_id", table_name="webhook_deliveries")
        op.drop_table("webhook_deliveries")
    if _has_table(bind, "webhooks"):
        op.drop_table("webhooks")
