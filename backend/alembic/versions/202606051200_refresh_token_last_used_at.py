"""Add refresh_tokens.last_used_at - per-session last-activity timestamp.

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
from app.db_guards import _has_column

revision = "202606051200"
down_revision = "202606031500"
branch_labels = None
depends_on = None


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
