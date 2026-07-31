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
from app.db_guards import _has_column

revision = "202606120001"
down_revision = "202606110001"
branch_labels = None
depends_on = None


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
