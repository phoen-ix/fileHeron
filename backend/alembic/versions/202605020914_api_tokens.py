"""api_tokens

Revision ID: 202605020914
Revises: 202605020913
Create Date: 2026-05-02 09:14:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020914"
down_revision: str | None = "202605020913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(8), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_tokens_owner_user_id", "api_tokens", ["owner_user_id"])
    op.create_index("ix_api_tokens_prefix", "api_tokens", ["prefix"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_prefix", table_name="api_tokens")
    op.drop_index("ix_api_tokens_owner_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
