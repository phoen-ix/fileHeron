"""email_change_tokens — pending email-change staging (v1.13.0).

Stages a pending email change (target address + per-side confirmation flags)
so ``users.email`` is only mutated once the new address — and, in
``verify_both`` mode, the old address too — has proven control. Mirrors the
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

revision = "202606060001"
down_revision = "202606051500"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t LIMIT 1"
            ),
            {"t": table},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return rows is not None


def _has_index(bind, table: str, index: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND index_name = :i LIMIT 1"
            ),
            {"t": table, "i": index},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:i AND tbl_name=:t"
        ),
        {"i": index, "t": table},
    ).fetchone()
    return rows is not None


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
    for name, _cols, _unique in _INDEXES:
        if _has_index(bind, "email_change_tokens", name):
            op.drop_index(name, table_name="email_change_tokens")
    if _has_table(bind, "email_change_tokens"):
        op.drop_table("email_change_tokens")
