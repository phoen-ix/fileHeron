"""Per-share download limit (v1.1.0).

Adds `download_limit` + `downloads_remaining` to shares — mirrors the
existing public_link counter for authenticated shares. NULL = unlimited
so existing rows keep their current behavior with no back-fill.

Revision ID: 202605161900
Revises: 202605030001
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202605161900"
down_revision = "202605030001"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "shares", "download_limit"):
        op.add_column(
            "shares",
            sa.Column("download_limit", sa.Integer(), nullable=True),
        )
    if not _has_column(bind, "shares", "downloads_remaining"):
        op.add_column(
            "shares",
            sa.Column("downloads_remaining", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "shares", "downloads_remaining"):
        op.drop_column("shares", "downloads_remaining")
    if _has_column(bind, "shares", "download_limit"):
        op.drop_column("shares", "download_limit")
