"""users.admin_nav_collapse_mode + admin_nav_open_categories — per-admin
collapsible sidebar preference.

Revision ID: 202606060002
Revises: 202606060001
Create Date: 2026-06-06 00:02:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202606060002"
down_revision: str | None = "202606060001"
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
    # VARCHAR(20) matches SAEnum(AdminNavCollapseMode, native_enum=False,
    # length=20) on MariaDB. NULL = system default (accordion).
    if not _has_column(bind, "users", "admin_nav_collapse_mode"):
        op.add_column(
            "users",
            sa.Column("admin_nav_collapse_mode", sa.String(20), nullable=True),
        )
    # JSON list of open category keys (mirrors invite_tokens.initial_group_ids).
    # NULL = never set; [] = explicit all-collapsed.
    if not _has_column(bind, "users", "admin_nav_open_categories"):
        op.add_column(
            "users",
            sa.Column("admin_nav_open_categories", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "admin_nav_open_categories"):
        op.drop_column("users", "admin_nav_open_categories")
    if _has_column(bind, "users", "admin_nav_collapse_mode"):
        op.drop_column("users", "admin_nav_collapse_mode")
