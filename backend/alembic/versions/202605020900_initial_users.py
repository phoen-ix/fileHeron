"""initial users

Revision ID: 202605020900
Revises:
Create Date: 2026-05-02 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020900"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("email_hint", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="client"),
        sa.Column("locale", sa.String(2), nullable=False, server_default="en"),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oidc_subject", sa.String(255), nullable=True),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_users_email_hash", "users", ["email_hash"], unique=True)
    op.create_index("ix_users_oidc_subject", "users", ["oidc_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_oidc_subject", table_name="users")
    op.drop_index("ix_users_email_hash", table_name="users")
    op.drop_table("users")
