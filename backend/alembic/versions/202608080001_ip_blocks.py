"""ip_blocks - temporary source blocks for the scan guard (v2.10.0).

Backs `models/ip_block.py`. Additive: a new table only, no change to any existing
one, and the feature that writes it ships DISABLED (`scan_guard.enabled` defaults
false), so an upgrade is behaviour-neutral until an admin opts in.

Each op is guarded SEPARATELY. Nesting the index creations inside the
`_has_table` guard means a crash between the table and its indexes skips them
forever on the retry - `tests/test_migration_reruns.py` fails if a revision
reintroduces that shape.

Revision ID: 202608080001
Revises: 202608070002
Create Date: 2026-08-08
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202608080001"
down_revision = "202608070002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "ip_blocks"):
        op.create_table(
            "ip_blocks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("subject", sa.String(length=64), nullable=False),
            sa.Column("network", sa.String(length=64), nullable=False),
            sa.Column(
                "is_network", sa.Boolean(), nullable=False, server_default="0"
            ),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column(
                "source", sa.String(length=8), nullable=False, server_default="auto"
            ),
            sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("strikes", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_path", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            # No FK on purpose: forensic, and must survive the actor's erasure -
            # same reasoning as error_log.user_id.
            sa.Column("released_by_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
        )
    if not _has_index(bind, "ip_blocks", "ix_ip_blocks_expires_at"):
        op.create_index("ix_ip_blocks_expires_at", "ip_blocks", ["expires_at"])
    if not _has_index(bind, "ip_blocks", "ix_ip_blocks_subject_created"):
        op.create_index(
            "ix_ip_blocks_subject_created", "ip_blocks", ["subject", "created_at"]
        )
    if not _has_index(bind, "ip_blocks", "ix_ip_blocks_network_created"):
        op.create_index(
            "ix_ip_blocks_network_created", "ip_blocks", ["network", "created_at"]
        )
    if not _has_index(bind, "ip_blocks", "ix_ip_blocks_created_at"):
        op.create_index("ix_ip_blocks_created_at", "ip_blocks", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "ix_ip_blocks_created_at",
        "ix_ip_blocks_network_created",
        "ix_ip_blocks_subject_created",
        "ix_ip_blocks_expires_at",
    ):
        if _has_index(bind, "ip_blocks", name):
            op.drop_index(name, table_name="ip_blocks")
    if _has_table(bind, "ip_blocks"):
        op.drop_table("ip_blocks")
