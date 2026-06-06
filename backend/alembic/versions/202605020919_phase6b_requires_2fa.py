"""phase6b - users.requires_2fa_setup

Revision ID: 202605020919
Revises: 202605020918
Create Date: 2026-05-02 09:19:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020919"
down_revision: str | None = "202605020918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "requires_2fa_setup",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "requires_2fa_setup")
