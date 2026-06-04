"""Add refresh_tokens.last_used_at — per-session last-activity timestamp.

Session = a `refresh_tokens` row. The token rotates on every refresh (a new
head row is minted), so `created_at` is threaded forward to mean the original
sign-in time while `last_used_at` advances to the latest rotation. Admins sort
by `last_used_at` to spot stale/hanging sessions. Existing rows are backfilled
to their `created_at` so they don't read as "never used".

Revision ID: 202606051200
Revises: 202606031500
Create Date: 2026-06-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606051200"
down_revision = "202606031500"
branch_labels = None
depends_on = None


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
    if not _has_column(bind, "refresh_tokens", "last_used_at"):
        op.add_column(
            "refresh_tokens", sa.Column("last_used_at", sa.DateTime(), nullable=True)
        )
        op.execute(
            "UPDATE refresh_tokens SET last_used_at = created_at "
            "WHERE last_used_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "refresh_tokens", "last_used_at"):
        op.drop_column("refresh_tokens", "last_used_at")
