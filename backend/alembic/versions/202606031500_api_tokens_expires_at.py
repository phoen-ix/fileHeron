"""Add api_tokens.expires_at - optional API-token expiry.

NULL = never expires (the existing behaviour, so all current tokens are
unaffected). When set, verify_token rejects the token past that instant with
API_TOKEN_EXPIRED. No backfill - existing rows stay NULL.

Revision ID: 202606031500
Revises: 202605312000
Create Date: 2026-06-03
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202606031500"
down_revision = "202605312000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "api_tokens", "expires_at"):
        op.add_column(
            "api_tokens", sa.Column("expires_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "api_tokens", "expires_at"):
        op.drop_column("api_tokens", "expires_at")
