"""invite_tokens

Revision ID: 202605020901
Revises: 202605020900
Create Date: 2026-05-02 09:01:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020901"
down_revision: str | None = "202605020900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email_hint", sa.String(120), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("target_role", sa.String(20), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["used_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_invite_tokens_token_hash", "invite_tokens", ["token_hash"], unique=True)
    op.create_index("ix_invite_tokens_email_hash", "invite_tokens", ["email_hash"])


def downgrade() -> None:
    # drop_table removes the table's indexes with it; dropping them explicitly
    # first errors when a later migration already removed one (MySQL 1091 - the
    # email-plaintext migration dropped the email_hash index).
    op.drop_table("invite_tokens")
