"""phase8 — user_webauthn_credentials

Revision ID: 202605020920
Revises: 202605020919
Create Date: 2026-05-02 09:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202605020920"
down_revision: str | None = "202605020919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_webauthn_credentials",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(length=512), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(length=2048), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", sa.String(120), nullable=False, server_default=""),
        sa.Column("name", sa.String(120), nullable=False, server_default="passkey"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_user_webauthn_credentials_user_id",
        "user_webauthn_credentials",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_webauthn_credentials_user_id",
        table_name="user_webauthn_credentials",
    )
    op.drop_table("user_webauthn_credentials")
