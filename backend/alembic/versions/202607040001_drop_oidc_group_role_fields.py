"""Drop the OIDC group->role mapping fields (groups_claim / admin_groups /
employee_groups) from oidc_providers.

Roles are managed inside fileHeron, not by the identity provider: the group->role
sync was removed, so these columns are unused. The three columns were NOT NULL, so
they must be dropped (leaving them would break inserts once the ORM stops mapping
them).

Re-runnable via _has_column guards; uses batch_alter_table for SQLite portability.

Revision ID: 202607040001
Revises: 202606160001
Create Date: 2026-07-04
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607040001"
down_revision = "202606160001"
branch_labels = None
depends_on = None

_COLS = ("groups_claim", "admin_groups", "employee_groups")


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
    to_drop = [c for c in _COLS if _has_column(bind, "oidc_providers", c)]
    if not to_drop:
        return
    with op.batch_alter_table("oidc_providers") as batch:
        for c in to_drop:
            batch.drop_column(c)


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("oidc_providers") as batch:
        if not _has_column(bind, "oidc_providers", "groups_claim"):
            batch.add_column(
                sa.Column("groups_claim", sa.String(length=200), nullable=False,
                          server_default="groups")
            )
        if not _has_column(bind, "oidc_providers", "admin_groups"):
            batch.add_column(
                sa.Column("admin_groups", sa.Text(), nullable=False, server_default="")
            )
        if not _has_column(bind, "oidc_providers", "employee_groups"):
            batch.add_column(
                sa.Column("employee_groups", sa.Text(), nullable=False, server_default="")
            )
