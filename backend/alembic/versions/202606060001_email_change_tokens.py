"""email_change_tokens - pending email-change staging (v1.13.0).

Stages a pending email change (target address + per-side confirmation flags)
so ``users.email`` is only mutated once the new address - and, in
``verify_both`` mode, the old address too - has proven control. Mirrors the
``password_reset_tokens`` / ``email_verify_tokens`` token tables (plain
INTEGER PK, not BigInteger). Guards make it re-runnable after a partial
failure.

Revision ID: 202606060001
Revises: 202606051500
Create Date: 2026-06-06
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202606060001"
down_revision = "202606051500"
branch_labels = None
depends_on = None


_INDEXES = [
    ("ix_email_change_tokens_user_id", ["user_id"], False),
    ("ix_email_change_tokens_new_token_hash", ["new_token_hash"], True),
    ("ix_email_change_tokens_old_token_hash", ["old_token_hash"], True),
    ("ix_email_change_tokens_cancel_token_hash", ["cancel_token_hash"], True),
    ("ix_email_change_tokens_expires_at", ["expires_at"], False),
]


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "email_change_tokens"):
        op.create_table(
            "email_change_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("new_email", sa.String(254), nullable=False),
            sa.Column("new_token_hash", sa.String(64), nullable=False),
            sa.Column("old_token_hash", sa.String(64), nullable=True),
            sa.Column("cancel_token_hash", sa.String(64), nullable=True),
            sa.Column("new_confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("old_confirmed_at", sa.DateTime(), nullable=True),
            sa.Column(
                "oidc_mode", sa.String(16), nullable=False, server_default="reset_setpw"
            ),
            sa.Column("initiated_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["initiated_by_id"], ["users.id"], ondelete="SET NULL"
            ),
        )
    for name, cols, unique in _INDEXES:
        if not _has_index(bind, "email_change_tokens", name):
            op.create_index(name, "email_change_tokens", cols, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    # drop_table removes the table's indexes (incl. FK-backing ones) with it;
    # dropping a FK-backed index first errors (MySQL 1553).
    if _has_table(bind, "email_change_tokens"):
        op.drop_table("email_change_tokens")
