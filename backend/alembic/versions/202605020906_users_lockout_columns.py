"""users add lockout columns

Revision ID: 202605020906
Revises: 202605020905
Create Date: 2026-05-02 09:06:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020906"
down_revision: str | None = "202605020905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("lockout_email_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "lockout_email_sent_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
