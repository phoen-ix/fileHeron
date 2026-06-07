"""share-approval workflow - widen shares.state + approval tracking columns (v1.24.0).

Revision ID: 202606090001
Revises: 202606080001
Create Date: 2026-06-09

The `state` column was VARCHAR(10) (longest value "failed"). The new
"pending_approval" value is 16 chars, so widen to VARCHAR(20). Re-runnable:
the widen is idempotent and each add_column is `_has_column`-guarded.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606090001"
down_revision = "202606080001"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    # Widen state to fit "pending_approval" (16). Idempotent: 20→20 is a no-op.
    op.alter_column(
        "shares",
        "state",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
    )

    if not _has_column(bind, "shares", "approval_decided_by_id"):
        op.add_column(
            "shares",
            sa.Column("approval_decided_by_id", sa.Integer(), nullable=True),
        )
    # FK in its OWN step, not nested under the column guard: a partial-failure
    # rerun that already added the column above would otherwise skip the FK
    # forever (audit L26). No _has_fk helper exists, so tolerate "already exists".
    try:
        op.create_foreign_key(
            "fk_shares_approval_decided_by",
            "shares",
            "users",
            ["approval_decided_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
    except Exception:
        pass  # FK already present from a prior (possibly partial) run
    if not _has_column(bind, "shares", "approval_decided_at"):
        op.add_column(
            "shares", sa.Column("approval_decided_at", sa.DateTime(), nullable=True)
        )
    if not _has_column(bind, "shares", "rejection_reason"):
        op.add_column("shares", sa.Column("rejection_reason", sa.Text(), nullable=True))
    if not _has_column(bind, "shares", "notify_on_activation"):
        op.add_column(
            "shares", sa.Column("notify_on_activation", sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "shares", "approval_decided_by_id"):
        op.drop_constraint("fk_shares_approval_decided_by", "shares", type_="foreignkey")
        op.drop_column("shares", "approval_decided_by_id")
    for col in ("approval_decided_at", "rejection_reason", "notify_on_activation"):
        if _has_column(bind, "shares", col):
            op.drop_column("shares", col)
    op.alter_column(
        "shares",
        "state",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
