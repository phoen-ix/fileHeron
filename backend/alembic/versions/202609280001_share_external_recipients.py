"""share_external_recipients - addresses with no account a share was sent to (v2.21.0).

Backs `models/share_external_recipient.py`. Additive: a new table only, and the
feature that writes it ships DISABLED (`share.external_recipients.enabled`
defaults false), so an upgrade is behaviour-neutral until an admin opts in.

`email` carries the same binary collation as `users.email` (migration
202609240001): addresses are normalised on write, and the unique key must not
fold two distinct addresses together.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202609280001
Revises: 202609240001
Create Date: 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202609280001"
down_revision = "202609240001"
branch_labels = None
depends_on = None

_TABLE = "share_external_recipients"
_EMAIL = sa.String(254).with_variant(sa.String(254, collation="utf8mb4_bin"), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column(
                "share_id",
                sa.String(length=36),
                sa.ForeignKey("shares.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("email", _EMAIL, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("notified_at", sa.DateTime(), nullable=True),
        )
    if not _has_index(bind, _TABLE, "ix_share_external_recipients_share_id"):
        op.create_index(
            "ix_share_external_recipients_share_id", _TABLE, ["share_id"]
        )
    if not _has_index(bind, _TABLE, "uq_share_external_recipients_share_email"):
        op.create_index(
            "uq_share_external_recipients_share_email",
            _TABLE,
            ["share_id", "email"],
            unique=True,
        )


def downgrade() -> None:
    # The table only: its indexes go with it, and MariaDB refuses to drop
    # `ix_..._share_id` on its own while the share_id foreign key needs it.
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        op.drop_table(_TABLE)
