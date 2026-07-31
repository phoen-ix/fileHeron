"""api_tokens.disabled_at - reversible disable distinct from revoke

Revision ID: 202605020924
Revises: 202605020923
Create Date: 2026-05-02 09:24:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision: str = "202605020924"
down_revision: str | None = "202605020923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "api_tokens", "disabled_at"):
        op.add_column(
            "api_tokens",
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "api_tokens", "disabled_at"):
        op.drop_column("api_tokens", "disabled_at")
