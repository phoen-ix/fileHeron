"""user_totp

Revision ID: 202605020907
Revises: 202605020906
Create Date: 2026-05-02 09:07:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020907"
down_revision: str | None = "202605020906"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_totp",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("secret_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("user_totp")
