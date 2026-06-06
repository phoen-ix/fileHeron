"""invite_tokens.initial_group_ids — pre-assign groups at invite time

Revision ID: 202605020923
Revises: 202605020922
Create Date: 2026-05-02 09:23:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020923"
down_revision: str | None = "202605020922"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind, table: str, column: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "invite_tokens", "initial_group_ids"):
        op.add_column(
            "invite_tokens",
            sa.Column("initial_group_ids", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "invite_tokens", "initial_group_ids"):
        op.drop_column("invite_tokens", "initial_group_ids")
