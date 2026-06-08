"""login_attempts

Revision ID: 202605020909
Revises: 202605020908
Create Date: 2026-05-02 09:09:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020909"
down_revision: str | None = "202605020908"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email_hash", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column(
            "attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
    )
    op.create_index("ix_login_attempts_email_hash", "login_attempts", ["email_hash"])
    op.create_index("ix_login_attempts_ip", "login_attempts", ["ip"])
    op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"])
    op.create_index("ix_login_attempts_outcome", "login_attempts", ["outcome"])


def downgrade() -> None:
    # drop_table removes the table's indexes with it; dropping them explicitly
    # first errors when a later migration already removed one (MySQL 1091 - the
    # email-plaintext migration dropped the email_hash index).
    op.drop_table("login_attempts")
