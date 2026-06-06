"""phase5 - public_links + public_link_password_attempts

Revision ID: 202605020917
Revises: 202605020916
Create Date: 2026-05-02 09:17:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020917"
down_revision: str | None = "202605020916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("share_id", sa.String(36), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("download_limit", sa.Integer(), nullable=True),
        sa.Column("downloads_remaining", sa.Integer(), nullable=True),
        sa.Column(
            "notify_on_download",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["share_id"], ["shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "public_link_password_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("public_link_id", sa.String(36), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column(
            "attempted_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["public_link_id"], ["public_links.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_public_link_password_attempts_link_id",
        "public_link_password_attempts",
        ["public_link_id"],
    )
    op.create_index(
        "ix_public_link_password_attempts_ip",
        "public_link_password_attempts",
        ["ip"],
    )
    op.create_index(
        "ix_public_link_password_attempts_attempted_at",
        "public_link_password_attempts",
        ["attempted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_link_password_attempts_attempted_at",
        table_name="public_link_password_attempts",
    )
    op.drop_index(
        "ix_public_link_password_attempts_ip",
        table_name="public_link_password_attempts",
    )
    op.drop_index(
        "ix_public_link_password_attempts_link_id",
        table_name="public_link_password_attempts",
    )
    op.drop_table("public_link_password_attempts")
    op.drop_table("public_links")
