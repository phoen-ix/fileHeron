"""Add api_tokens.scopes - per-token least-privilege scopes.

NULL = unrestricted (full access = the existing behaviour, so all current
tokens are unaffected). When set, it holds a JSON array of granted scope names
and the token is confined to exactly those; out-of-scope requests get 403
INSUFFICIENT_SCOPE. No backfill - existing rows stay NULL.

Revision ID: 202606120001
Revises: 202606110001
Create Date: 2026-06-07
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606120001"
down_revision = "202606110001"
branch_labels = None
depends_on = None


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
    if not _has_column(bind, "api_tokens", "scopes"):
        op.add_column(
            "api_tokens", sa.Column("scopes", sa.String(length=1024), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "api_tokens", "scopes"):
        op.drop_column("api_tokens", "scopes")
