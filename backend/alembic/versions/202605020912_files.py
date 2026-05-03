"""files

Revision ID: 202605020912
Revises: 202605020911
Create Date: 2026-05-02 09:12:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020912"
down_revision: str | None = "202605020911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("share_id", sa.String(36), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=True),
        sa.Column("sha256_hex", sa.String(64), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="uploading"),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tus_upload_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["share_id"], ["shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_files_share_id", "files", ["share_id"])
    op.create_index("ix_files_uploaded_by_id", "files", ["uploaded_by_id"])
    op.create_index("ix_files_state", "files", ["state"])
    op.create_index("ix_files_sha256_hex", "files", ["sha256_hex"])
    op.create_index("ix_files_tus_upload_id", "files", ["tus_upload_id"])


def downgrade() -> None:
    op.drop_index("ix_files_tus_upload_id", table_name="files")
    op.drop_index("ix_files_sha256_hex", table_name="files")
    op.drop_index("ix_files_state", table_name="files")
    op.drop_index("ix_files_uploaded_by_id", table_name="files")
    op.drop_index("ix_files_share_id", table_name="files")
    op.drop_table("files")
