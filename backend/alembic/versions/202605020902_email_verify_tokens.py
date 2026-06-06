"""email_verify_tokens

Revision ID: 202605020902
Revises: 202605020901
Create Date: 2026-05-02 09:02:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020902"
down_revision: str | None = "202605020901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_verify_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_email_verify_tokens_user_id", "email_verify_tokens", ["user_id"])
    op.create_index("ix_email_verify_tokens_token_hash", "email_verify_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_email_verify_tokens_token_hash", table_name="email_verify_tokens")
    op.drop_index("ix_email_verify_tokens_user_id", table_name="email_verify_tokens")
    op.drop_table("email_verify_tokens")
