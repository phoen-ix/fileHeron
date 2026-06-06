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

revision: str = "202605031400"
down_revision: str | None = "202605021000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    if not _has_column(bind, "public_links", "token_encrypted"):
        op.add_column(
            "public_links",
            sa.Column("token_encrypted", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "public_links", "token_encrypted"):
        op.drop_column("public_links", "token_encrypted")
