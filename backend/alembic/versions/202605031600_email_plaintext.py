"""Replace HMAC email_hash + masked email_hint with plaintext email
columns on users + invite_tokens + login_attempts. Existing rows are
populated with a unique-per-row placeholder so the NOT NULL UNIQUE
constraint applies cleanly; admin renames real users via SQL or the
admin UI after the migration. No reverse migration of data is
possible (Fernet/SHA-256 are one-way; the plaintext is gone).

Revision ID: 202605031600
Revises: 202605031400
Create Date: 2026-05-03 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605031600"
down_revision: str | None = "202605031400"
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


def _drop_constraints_referencing_column(bind, table: str, column: str) -> None:
    """MySQL/MariaDB: an indexed column can't be dropped while indexes /
    unique constraints reference it. Drop those first. SQLite handles
    column drops without explicit index drops via the batch op."""
    if bind.dialect.name != "mysql":
        return
    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchall()
    for (index_name,) in rows:
        if index_name == "PRIMARY":
            continue
        bind.execute(sa.text(f"ALTER TABLE {table} DROP INDEX {index_name}"))


def upgrade() -> None:
    bind = op.get_bind()

    # ---- users -----------------------------------------------------------
    if not _has_column(bind, "users", "email"):
        op.add_column(
            "users",
            sa.Column("email", sa.String(length=254), nullable=True),
        )
        # Backfill placeholder per-row so the UNIQUE NOT NULL constraint
        # below applies. Real users are renamed via SQL / admin UI after.
        bind.execute(
            sa.text(
                "UPDATE users SET email = CONCAT('legacy-', id, '@placeholder.invalid') "
                "WHERE email IS NULL"
            )
        )
        # Tighten to NOT NULL UNIQUE.
        if bind.dialect.name == "mysql":
            op.alter_column(
                "users", "email",
                existing_type=sa.String(length=254),
                nullable=False,
            )
            op.create_index("ix_users_email", "users", ["email"], unique=True)
        else:
            with op.batch_alter_table("users") as batch:
                batch.alter_column("email", nullable=False)
                batch.create_index("ix_users_email", ["email"], unique=True)

    if _has_column(bind, "users", "email_hash"):
        _drop_constraints_referencing_column(bind, "users", "email_hash")
        op.drop_column("users", "email_hash")
    if _has_column(bind, "users", "email_hint"):
        op.drop_column("users", "email_hint")

    # ---- invite_tokens ---------------------------------------------------
    if not _has_column(bind, "invite_tokens", "email"):
        op.add_column(
            "invite_tokens",
            sa.Column("email", sa.String(length=254), nullable=True),
        )
        bind.execute(
            sa.text(
                "UPDATE invite_tokens SET email = "
                "CONCAT('legacy-invite-', id, '@placeholder.invalid') "
                "WHERE email IS NULL"
            )
        )
        if bind.dialect.name == "mysql":
            op.alter_column(
                "invite_tokens", "email",
                existing_type=sa.String(length=254),
                nullable=False,
            )
            op.create_index("ix_invite_tokens_email", "invite_tokens", ["email"])
        else:
            with op.batch_alter_table("invite_tokens") as batch:
                batch.alter_column("email", nullable=False)
                batch.create_index("ix_invite_tokens_email", ["email"])

    if _has_column(bind, "invite_tokens", "email_hash"):
        _drop_constraints_referencing_column(bind, "invite_tokens", "email_hash")
        op.drop_column("invite_tokens", "email_hash")
    if _has_column(bind, "invite_tokens", "email_hint"):
        op.drop_column("invite_tokens", "email_hint")

    # ---- login_attempts --------------------------------------------------
    if not _has_column(bind, "login_attempts", "email"):
        op.add_column(
            "login_attempts",
            sa.Column("email", sa.String(length=254), nullable=True),
        )
        op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    if _has_column(bind, "login_attempts", "email_hash"):
        _drop_constraints_referencing_column(bind, "login_attempts", "email_hash")
        op.drop_column("login_attempts", "email_hash")


def downgrade() -> None:
    """Best-effort reverse: we can't restore the original plaintext-free
    posture, but we can re-add the dropped columns so an older revision
    of the code can run (it will see NULL hashes and fail logins)."""
    bind = op.get_bind()
    for table, col, length in [
        ("users", "email_hash", 64),
        ("users", "email_hint", 120),
        ("invite_tokens", "email_hash", 64),
        ("invite_tokens", "email_hint", 120),
        ("login_attempts", "email_hash", 64),
    ]:
        if not _has_column(bind, table, col):
            op.add_column(table, sa.Column(col, sa.String(length=length), nullable=True))
    if _has_column(bind, "users", "email"):
        op.drop_column("users", "email")
    if _has_column(bind, "invite_tokens", "email"):
        op.drop_column("invite_tokens", "email")
    if _has_column(bind, "login_attempts", "email"):
        op.drop_column("login_attempts", "email")
