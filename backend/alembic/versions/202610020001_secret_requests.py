"""secret requests - asking someone for a password or other short secret (v2.24.0).

Backs `models/secret_request.py`. Three new tables, and on `secrets`:

- `created_by_id` becomes NULLable: an answer written by someone without an
  account (through a mailed request link or the copyable one) has no sender.
- `request_id` (FK, SET NULL), `is_answer`, `answered_by_email`: an answer stands
  on its own once its request is pruned.
- `req_kdf_salt`, `req_kdf_params`, `req_ephemeral_key`, `has_request_passphrase`:
  the requester's optional passphrase layer (utils/crypto.py).

Additive for every existing row, and the feature is behind the same
`secrets.enabled` switch, which ships OFF.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202610020001
Revises: 202610010001
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _column_nullable, _has_column, _has_foreign_key, _has_index, _has_table

revision = "202610020001"
down_revision = "202610010001"
branch_labels = None
depends_on = None

_EMAIL = sa.String(254).with_variant(sa.String(254, collation="utf8mb4_bin"), "mysql")
_FK_SECRET_REQUEST = "fk_secrets_request_id"


def _index(bind, table: str, name: str, columns: list[str], *, unique: bool = False) -> None:
    if not _has_index(bind, table, name):
        op.create_index(name, table, columns, unique=unique)


def _add_column(bind, table: str, column: sa.Column) -> None:
    if not _has_column(bind, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "secret_requests"):
        op.create_table(
            "secret_requests",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "requester_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("label", sa.String(length=200), nullable=False),
            sa.Column("note", sa.String(length=1000), nullable=True),
            sa.Column("state", sa.String(length=10), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("answer_max_views", sa.Integer(), nullable=True),
            sa.Column("answer_expires_in_sec", sa.Integer(), nullable=True),
            sa.Column("kdf_salt", sa.String(length=64), nullable=True),
            sa.Column("kdf_params", sa.String(length=64), nullable=True),
            sa.Column("public_key", sa.String(length=64), nullable=True),
            sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
            sa.Column(
                "fulfilled_by_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("fulfilled_via_target_id", sa.Integer(), nullable=True),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _index(bind, "secret_requests", "ix_secret_requests_requester_id", ["requester_id"])
    _index(bind, "secret_requests", "ix_secret_requests_state", ["state"])
    _index(bind, "secret_requests", "ix_secret_requests_state_expires", ["state", "expires_at"])

    if not _has_table(bind, "secret_request_targets"):
        op.create_table(
            "secret_request_targets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "request_id",
                sa.String(length=36),
                sa.ForeignKey("secret_requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=8), nullable=False),
            sa.Column(
                "target_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "target_group_id",
                # groups.id is BIGINT; InnoDB refuses an INT foreign key to it.
                sa.BigInteger(),
                sa.ForeignKey("groups.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("email", _EMAIL, nullable=True),
            sa.Column("token_hash", sa.String(length=64), nullable=True),
            sa.Column("token_encrypted", sa.String(length=255), nullable=True),
            sa.Column("notified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    t = "secret_request_targets"
    _index(bind, t, "ix_secret_request_targets_request_id", ["request_id"])
    _index(bind, t, "ix_secret_request_targets_target_user_id", ["target_user_id"])
    _index(bind, t, "ix_secret_request_targets_target_group_id", ["target_group_id"])
    _index(bind, t, "ix_secret_request_targets_token_hash", ["token_hash"], unique=True)
    _index(bind, t, "uq_secret_request_targets_user", ["request_id", "target_user_id"], unique=True)
    _index(bind, t, "uq_secret_request_targets_group", ["request_id", "target_group_id"], unique=True)
    _index(bind, t, "uq_secret_request_targets_email", ["request_id", "email"], unique=True)

    if not _has_table(bind, "secret_request_group_members"):
        op.create_table(
            "secret_request_group_members",
            sa.Column(
                "target_id",
                sa.Integer(),
                sa.ForeignKey("secret_request_targets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("target_id", "user_id"),
        )
    _index(
        bind,
        "secret_request_group_members",
        "ix_secret_request_group_members_user_id",
        ["user_id"],
    )

    # --- secrets: answers ---
    if _has_column(bind, "secrets", "created_by_id") and not _column_nullable(
        bind, "secrets", "created_by_id"
    ):
        op.alter_column(
            "secrets",
            "created_by_id",
            existing_type=sa.Integer(),
            nullable=True,
            existing_nullable=False,
        )
    _add_column(bind, "secrets", sa.Column("request_id", sa.String(length=36), nullable=True))
    if bind.dialect.name != "sqlite" and not _has_foreign_key(bind, "secrets", _FK_SECRET_REQUEST):
        op.create_foreign_key(
            _FK_SECRET_REQUEST,
            "secrets",
            "secret_requests",
            ["request_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _index(bind, "secrets", "ix_secrets_request_id", ["request_id"])
    _add_column(
        bind,
        "secrets",
        sa.Column("is_answer", sa.Boolean(), nullable=False, server_default="0"),
    )
    _add_column(bind, "secrets", sa.Column("answered_by_email", _EMAIL, nullable=True))
    _add_column(bind, "secrets", sa.Column("req_kdf_salt", sa.String(length=64), nullable=True))
    _add_column(bind, "secrets", sa.Column("req_kdf_params", sa.String(length=64), nullable=True))
    _add_column(
        bind, "secrets", sa.Column("req_ephemeral_key", sa.String(length=64), nullable=True)
    )
    _add_column(
        bind,
        "secrets",
        sa.Column("has_request_passphrase", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite" and _has_foreign_key(bind, "secrets", _FK_SECRET_REQUEST):
        op.drop_constraint(_FK_SECRET_REQUEST, "secrets", type_="foreignkey")
    if _has_index(bind, "secrets", "ix_secrets_request_id"):
        op.drop_index("ix_secrets_request_id", table_name="secrets")
    for column in (
        "request_id",
        "is_answer",
        "answered_by_email",
        "req_kdf_salt",
        "req_kdf_params",
        "req_ephemeral_key",
        "has_request_passphrase",
    ):
        if _has_column(bind, "secrets", column):
            op.drop_column("secrets", column)
    if _has_column(bind, "secrets", "created_by_id") and _column_nullable(
        bind, "secrets", "created_by_id"
    ):
        # Answers without a sender cannot exist below this revision.
        op.execute(sa.text("DELETE FROM secrets WHERE created_by_id IS NULL"))
        op.alter_column(
            "secrets",
            "created_by_id",
            existing_type=sa.Integer(),
            nullable=False,
            existing_nullable=True,
        )
    # Tables only, children first (their indexes go with them).
    for table in ("secret_request_group_members", "secret_request_targets", "secret_requests"):
        if _has_table(bind, table):
            op.drop_table(table)
