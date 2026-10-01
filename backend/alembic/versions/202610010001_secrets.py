"""secrets - passwords and other short secrets with a view limit and/or expiry (v2.24.0).

Backs `models/secret.py`. Additive: five new tables, and the feature that writes
them ships DISABLED (`secrets.enabled` defaults false), so an upgrade is
behaviour-neutral until an admin turns it on.

`secret_recipients.email` carries the same binary collation as `users.email`
(migration 202609240001): the unique key must not fold two distinct addresses
together.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202610010001
Revises: 202609280003
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202610010001"
down_revision = "202609280003"
branch_labels = None
depends_on = None

_EMAIL = sa.String(254).with_variant(sa.String(254, collation="utf8mb4_bin"), "mysql")
_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _index(bind, table: str, name: str, columns: list[str], *, unique: bool = False) -> None:
    if not _has_index(bind, table, name):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "secrets"):
        op.create_table(
            "secrets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "created_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("label", sa.String(length=200), nullable=True),
            sa.Column("ciphertext", sa.Text(), nullable=True),
            sa.Column("key_encrypted", sa.Text(), nullable=True),
            sa.Column("kdf_salt", sa.String(length=64), nullable=True),
            sa.Column("kdf_params", sa.String(length=64), nullable=True),
            sa.Column("has_passphrase", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("max_views", sa.Integer(), nullable=True),
            sa.Column("view_scope", sa.String(length=16), nullable=False),
            sa.Column("views_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("notify_on_view", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("burn_after_failures", sa.Integer(), nullable=True),
            sa.Column("state", sa.String(length=10), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _index(bind, "secrets", "ix_secrets_created_by_id", ["created_by_id"])
    _index(bind, "secrets", "ix_secrets_state", ["state"])
    _index(bind, "secrets", "ix_secrets_state_expires", ["state", "expires_at"])

    if not _has_table(bind, "secret_recipients"):
        op.create_table(
            "secret_recipients",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "secret_id",
                sa.String(length=36),
                sa.ForeignKey("secrets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=8), nullable=False),
            sa.Column(
                "recipient_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "recipient_group_id",
                # groups.id is BIGINT; InnoDB refuses an INT foreign key to it.
                sa.BigInteger(),
                sa.ForeignKey("groups.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("email", _EMAIL, nullable=True),
            sa.Column("token_hash", sa.String(length=64), nullable=True),
            sa.Column("token_encrypted", sa.String(length=255), nullable=True),
            sa.Column("views_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.Column("burned_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("notified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _index(bind, "secret_recipients", "ix_secret_recipients_secret_id", ["secret_id"])
    _index(
        bind, "secret_recipients", "ix_secret_recipients_recipient_user_id", ["recipient_user_id"]
    )
    _index(
        bind,
        "secret_recipients",
        "ix_secret_recipients_recipient_group_id",
        ["recipient_group_id"],
    )
    _index(
        bind,
        "secret_recipients",
        "ix_secret_recipients_token_hash",
        ["token_hash"],
        unique=True,
    )
    _index(
        bind,
        "secret_recipients",
        "uq_secret_recipients_user",
        ["secret_id", "recipient_user_id"],
        unique=True,
    )
    _index(
        bind,
        "secret_recipients",
        "uq_secret_recipients_group",
        ["secret_id", "recipient_group_id"],
        unique=True,
    )
    _index(
        bind,
        "secret_recipients",
        "uq_secret_recipients_email",
        ["secret_id", "email"],
        unique=True,
    )

    if not _has_table(bind, "secret_group_members"):
        op.create_table(
            "secret_group_members",
            sa.Column(
                "recipient_id",
                sa.Integer(),
                sa.ForeignKey("secret_recipients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("recipient_id", "user_id"),
        )
    _index(bind, "secret_group_members", "ix_secret_group_members_user_id", ["user_id"])

    if not _has_table(bind, "secret_user_states"):
        op.create_table(
            "secret_user_states",
            sa.Column(
                "secret_id",
                sa.String(length=36),
                sa.ForeignKey("secrets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("views_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("burned_at", sa.DateTime(), nullable=True),
            sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("secret_id", "user_id"),
        )
    _index(bind, "secret_user_states", "ix_secret_user_states_user_id", ["user_id"])

    if not _has_table(bind, "secret_access_events"):
        op.create_table(
            "secret_access_events",
            sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
            sa.Column(
                "secret_id",
                sa.String(length=36),
                sa.ForeignKey("secrets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "recipient_id",
                sa.Integer(),
                sa.ForeignKey("secret_recipients.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.Column("outcome", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _index(bind, "secret_access_events", "ix_secret_access_events_secret_id", ["secret_id"])
    _index(
        bind, "secret_access_events", "ix_secret_access_events_recipient_id", ["recipient_id"]
    )
    _index(bind, "secret_access_events", "ix_secret_access_events_user_id", ["user_id"])
    _index(
        bind, "secret_access_events", "ix_secret_access_events_created_at", ["created_at"]
    )


def downgrade() -> None:
    # Tables only, children first: their indexes go with them, and MariaDB
    # refuses to drop an index a foreign key still needs.
    bind = op.get_bind()
    for table in (
        "secret_access_events",
        "secret_user_states",
        "secret_group_members",
        "secret_recipients",
        "secrets",
    ):
        if _has_table(bind, table):
            op.drop_table(table)
