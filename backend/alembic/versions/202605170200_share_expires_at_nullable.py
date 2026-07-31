"""Allow shares.expires_at to be NULL = "never expires" (v1.1.4).

Cron filters using `WHERE expires_at < now()` or
`WHERE expires_at IN (now+24h, now+25h)` exclude NULL rows by SQL
NULL semantics, so never-expire shares simply sit untouched - no
worker logic change is needed.

Revision ID: 202605170200
Revises: 202605161900
Create Date: 2026-05-17
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _column_nullable, _has_column

revision = "202605170200"
down_revision = "202605161900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # `_has_column` first: the shared guard answers False for an absent column
    # (it is asked "does this still need tightening"), where this revision's
    # local copy answered None. Without the existence check the alter would be
    # attempted against a column that is not there.
    if _has_column(bind, "shares", "expires_at") and not _column_nullable(
        bind, "shares", "expires_at"
    ):
        op.alter_column(
            "shares",
            "expires_at",
            existing_type=sa.DateTime(),
            nullable=True,
            existing_nullable=False,
        )


def downgrade() -> None:
    # Reverting requires every existing NULL row to be back-filled - the
    # operator must decide on the policy. We refuse to silently invent a
    # default (e.g. now+1y), so the downgrade is a no-op and a comment
    # is the contract.
    pass
