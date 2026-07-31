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
from app.db_guards import _column_nullable, _has_column, _has_index

revision: str = "202605031600"
down_revision: str | None = "202605031400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

    # Every step below is guarded independently. Nesting the NOT NULL + UNIQUE
    # tightening inside the add_column guard meant a crash between the two (DDL
    # auto-commits on MariaDB, and alembic_version is not bumped until the
    # revision returns) left `users.email` nullable and NOT unique forever: the
    # rerun saw the column already present and skipped the whole block, so the
    # uniqueness this migration exists to establish was silently absent (audit
    # 2026-07-30).

    # ---- users -----------------------------------------------------------
    if not _has_column(bind, "users", "email"):
        op.add_column(
            "users",
            sa.Column("email", sa.String(length=254), nullable=True),
        )
    if _has_column(bind, "users", "email"):
        # Backfill placeholder per-row so the UNIQUE NOT NULL constraint
        # below applies. Real users are renamed via SQL / admin UI after.
        # Idempotent: WHERE email IS NULL matches nothing on a rerun.
        bind.execute(
            sa.text(
                "UPDATE users SET email = CONCAT('legacy-', id, '@placeholder.invalid') "
                "WHERE email IS NULL"
            )
        )
        # Tighten to NOT NULL UNIQUE.
        if bind.dialect.name == "mysql":
            if _column_nullable(bind, "users", "email"):
                op.alter_column(
                    "users", "email",
                    existing_type=sa.String(length=254),
                    nullable=False,
                )
            if not _has_index(bind, "users", "ix_users_email"):
                op.create_index("ix_users_email", "users", ["email"], unique=True)
        else:
            if _column_nullable(bind, "users", "email"):
                with op.batch_alter_table("users") as batch:
                    batch.alter_column("email", nullable=False)
            if not _has_index(bind, "users", "ix_users_email"):
                op.create_index("ix_users_email", "users", ["email"], unique=True)

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
    if _has_column(bind, "invite_tokens", "email"):
        bind.execute(
            sa.text(
                "UPDATE invite_tokens SET email = "
                "CONCAT('legacy-invite-', id, '@placeholder.invalid') "
                "WHERE email IS NULL"
            )
        )
        if _column_nullable(bind, "invite_tokens", "email"):
            if bind.dialect.name == "mysql":
                op.alter_column(
                    "invite_tokens", "email",
                    existing_type=sa.String(length=254),
                    nullable=False,
                )
            else:
                with op.batch_alter_table("invite_tokens") as batch:
                    batch.alter_column("email", nullable=False)
        if not _has_index(bind, "invite_tokens", "ix_invite_tokens_email"):
            op.create_index("ix_invite_tokens_email", "invite_tokens", ["email"])

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
    if _has_column(bind, "login_attempts", "email") and not _has_index(
        bind, "login_attempts", "ix_login_attempts_email"
    ):
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
