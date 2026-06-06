"""Drop users.requires_2fa_setup — the static flag is replaced by
on-the-fly computation against the new admin-editable 2FA policy
(`twofa.required_roles` + `twofa.required_group_ids` in app_settings).

Revision ID: 202605021000
Revises: 202605020925
Create Date: 2026-05-02 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605021000"
down_revision: str | None = "202605020925"
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
    if _has_column(bind, "users", "requires_2fa_setup"):
        op.drop_column("users", "requires_2fa_setup")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "requires_2fa_setup"):
        op.add_column(
            "users",
            sa.Column(
                "requires_2fa_setup",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
