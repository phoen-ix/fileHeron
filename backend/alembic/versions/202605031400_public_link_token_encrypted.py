"""Add public_links.token_encrypted (Fernet ciphertext of the plaintext
token) so the share owner can re-view the URL on the share detail
page without revoking + recreating the link.

Lookup still uses the indexed token_hash column; this column is
owner-display only. Legacy rows stay NULL (Fernet is one-way without
the input).

Revision ID: 202605031400
Revises: 202605021000
Create Date: 2026-05-03 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision: str = "202605031400"
down_revision: str | None = "202605021000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "public_links", "token_encrypted"):
        op.add_column(
            "public_links",
            sa.Column("token_encrypted", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "public_links", "token_encrypted"):
        op.drop_column("public_links", "token_encrypted")
