"""phase4 - groups, group_members, client_employee_connections, share_recipients fk

Revision ID: 202605020916
Revises: 202605020915
Create Date: 2026-05-02 09:16:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020916"
down_revision: str | None = "202605020915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- groups ------------------------------------------------------------
    op.create_table(
        "groups",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_normalized", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_company_inbox",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_groups_is_company_inbox", "groups", ["is_company_inbox"])

    # ---- group_members -----------------------------------------------------
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    # ---- client_employee_connections --------------------------------------
    op.create_table(
        "client_employee_connections",
        sa.Column("client_user_id", sa.Integer(), nullable=False),
        sa.Column("employee_user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("client_user_id", "employee_user_id", "source"),
        sa.ForeignKeyConstraint(["client_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_client_employee_connections_client",
        "client_employee_connections",
        ["client_user_id"],
    )
    op.create_index(
        "ix_client_employee_connections_employee",
        "client_employee_connections",
        ["employee_user_id"],
    )

    # ---- share_recipients.recipient_group_id → real FK ---------------------
    # Drop the old non-FK column type (BigInteger) and recreate it as a real
    # FK with the same name. MariaDB needs the column kept; we add a
    # ForeignKeyConstraint instead of recreating the column.
    op.create_foreign_key(
        "fk_share_recipients_group",
        "share_recipients",
        "groups",
        ["recipient_group_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Drop the FK this migration added to the (surviving) share_recipients table
    # first, then the new tables. drop_table removes each table's own indexes
    # (incl. FK-backing ones) with it; dropping those explicitly first errors
    # (MySQL 1553). group_members is dropped before groups (it FKs to groups).
    op.drop_constraint(
        "fk_share_recipients_group", "share_recipients", type_="foreignkey"
    )
    op.drop_table("client_employee_connections")
    op.drop_table("group_members")
    op.drop_table("groups")
