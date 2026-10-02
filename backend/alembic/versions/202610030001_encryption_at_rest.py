"""encryption at rest - per-file data keys and the purge queue.

Backs the opt-in encryption of stored files (services/file_encryption.py,
utils/file_crypto.py). Additive and inert: every new column is NULL for every
existing row, NULL means "plaintext, read it as before", and the feature ships
OFF (`storage.encrypt_at_rest`).

- `files.enc_version`, `files.key_encrypted`, `files.release_verdict`, and an
  index over (enc_version, state) for the encryption lanes.
- `inbound_attachments.enc_version`, `inbound_attachments.key_encrypted`.
- `storage_purge_queue`: bytes waiting to be deleted after a committed swap.

Downgrade refuses while any row is encrypted: dropping the columns would drop
the keys, and the bytes would be unreadable for good.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202610030001
Revises: 202610020001
Create Date: 2026-10-03
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column, _has_index, _has_table

revision = "202610030001"
down_revision = "202610020001"
branch_labels = None
depends_on = None


def _add_column(bind, table: str, column: sa.Column) -> None:
    if not _has_column(bind, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column(bind, "files", sa.Column("enc_version", sa.SmallInteger(), nullable=True))
    _add_column(bind, "files", sa.Column("key_encrypted", sa.String(length=255), nullable=True))
    _add_column(bind, "files", sa.Column("release_verdict", sa.String(length=48), nullable=True))
    if not _has_index(bind, "files", "ix_files_enc_state"):
        op.create_index("ix_files_enc_state", "files", ["enc_version", "state"])

    _add_column(bind, "inbound_attachments", sa.Column("enc_version", sa.SmallInteger(), nullable=True))
    _add_column(
        bind, "inbound_attachments", sa.Column("key_encrypted", sa.String(length=255), nullable=True)
    )

    if not _has_table(bind, "storage_purge_queue"):
        op.create_table(
            "storage_purge_queue",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("locator", sa.String(length=512), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column("ref", sa.String(length=64), nullable=True),
            sa.Column("not_before", sa.DateTime(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if not _has_index(bind, "storage_purge_queue", "ix_storage_purge_queue_due"):
        op.create_index(
            "ix_storage_purge_queue_due", "storage_purge_queue", ["not_before", "id"]
        )


_ENCRYPTED_ROWS = {
    "files": "SELECT COUNT(*) FROM files WHERE enc_version IS NOT NULL",
    "inbound_attachments": "SELECT COUNT(*) FROM inbound_attachments WHERE enc_version IS NOT NULL",
}


def _refuse_if_encrypted(bind) -> None:
    """Below this revision nothing knows a file can be encrypted: the keys
    would be dropped with the columns, and every encrypted file served as
    ciphertext. Decrypt first (backend/scripts/decrypt_files_at_rest.py)."""
    for table, count_sql in _ENCRYPTED_ROWS.items():
        if not _has_column(bind, table, "enc_version"):
            continue
        n = bind.execute(sa.text(count_sql)).scalar()
        if n:
            raise RuntimeError(
                f"refusing to downgrade: {n} encrypted row(s) in {table}. "
                "Run backend/scripts/decrypt_files_at_rest.py first - dropping the "
                "columns would destroy their keys."
            )


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_if_encrypted(bind)
    if _has_table(bind, "storage_purge_queue"):
        op.drop_table("storage_purge_queue")
    for column in ("enc_version", "key_encrypted"):
        if _has_column(bind, "inbound_attachments", column):
            op.drop_column("inbound_attachments", column)
    if _has_index(bind, "files", "ix_files_enc_state"):
        op.drop_index("ix_files_enc_state", table_name="files")
    for column in ("enc_version", "key_encrypted", "release_verdict"):
        if _has_column(bind, "files", column):
            op.drop_column("files", column)
