"""users.email + invite_tokens.email: binary collation.

The database default, utf8mb4_unicode_ci, is case- AND accent-insensitive, so
`WHERE email = 'kevin@exämple.com'` matched the row `kevin@example.com`.
forgot-password looked the user up that way and then mailed the reset link to
the address the caller TYPED - a lookalike IDN domain anyone can register - so
one request handed over any account without enrolled TOTP, admins included.
The OIDC auto-link, the IMAP known-sender check and the mail footer's recipient
lookup trusted the same match.

`normalize_email` has lowercased every address on write since the plaintext
column was introduced, so binary equality is the contract the application
already assumed. Stored values are lowercased first, defensively: a row written
before normalisation would otherwise become unreachable by every lookup. That
UPDATE cannot collide on the UNIQUE key, because the case-insensitive key it
runs under already made two case-variants of one address impossible.

MariaDB only. SQLite (the test harness) compares TEXT in binary already, and the
model carries the collation as a `mysql` variant.

Revision ID: 202609240001
Revises: 202608150001
Create Date: 2026-09-24
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _column_collation

revision = "202609240001"
down_revision = "202608150001"
branch_labels = None
depends_on = None

_BINARY = "utf8mb4_bin"
_PREVIOUS = "utf8mb4_unicode_ci"
_COLUMNS = ("users", "invite_tokens")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    for table in _COLUMNS:
        current = _column_collation(bind, table, "email")
        if current is None or current == _BINARY:
            continue
        t = sa.table(table, sa.column("email"))
        bind.execute(
            sa.update(t)
            .where(t.c.email != sa.func.lower(t.c.email).collate(_BINARY))
            .values(email=sa.func.lower(t.c.email))
        )
        op.alter_column(
            table,
            "email",
            existing_type=sa.String(254),
            type_=sa.String(254, collation=_BINARY),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    for table in _COLUMNS:
        if _column_collation(bind, table, "email") != _BINARY:
            continue
        op.alter_column(
            table,
            "email",
            existing_type=sa.String(254, collation=_BINARY),
            type_=sa.String(254, collation=_PREVIOUS),
            existing_nullable=False,
        )
