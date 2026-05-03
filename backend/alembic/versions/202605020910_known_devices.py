"""known_devices

Revision ID: 202605020910
Revises: 202605020909
Create Date: 2026-05-02 09:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020910"
down_revision: str | None = "202605020909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "known_devices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ua_fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("ip_geohash", sa.String(8), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "ua_fingerprint_hash", "ip_geohash", name="uq_known_device"),
    )
    op.create_index("ix_known_devices_user_id", "known_devices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_known_devices_user_id", table_name="known_devices")
    op.drop_table("known_devices")
