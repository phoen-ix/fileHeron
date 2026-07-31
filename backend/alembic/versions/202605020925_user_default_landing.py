"""users.default_landing_page - per-user post-login destination

Revision ID: 202605020925
Revises: 202605020924
Create Date: 2026-05-02 09:25:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision: str = "202605020925"
down_revision: str | None = "202605020924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "default_landing_page"):
        op.add_column(
            "users",
            sa.Column("default_landing_page", sa.String(40), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "default_landing_page"):
        op.drop_column("users", "default_landing_page")
