"""inbound mailbox - inbound_messages + inbound_attachments (v1.27.0).

Stores messages fetched from the configured account over IMAP, plus their
attachments (bytes live in the storage backend; rows track AV state). Re-runnable:
guarded by `_has_table`.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_index, _has_table

revision = "202606110001"
down_revision = "202606100001"
branch_labels = None
depends_on = None


def _big_int():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _body():
    from sqlalchemy.dialects.mysql import LONGTEXT

    return sa.Text().with_variant(LONGTEXT(), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "inbound_messages"):
        op.create_table(
            "inbound_messages",
            sa.Column("id", _big_int(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("received_at", sa.DateTime(), nullable=True),
            sa.Column("sender_email", sa.String(length=320), nullable=False, index=True),
            sa.Column("sender_name", sa.String(length=255), nullable=True),
            sa.Column("sender_user_id", sa.Integer(), nullable=True, index=True),
            sa.Column("to_addr", sa.String(length=320), nullable=True),
            sa.Column("subject", sa.String(length=512), nullable=False),
            sa.Column("message_id", sa.String(length=320), nullable=True, index=True),
            sa.Column("in_reply_to", sa.String(length=320), nullable=True),
            sa.Column("imap_uid", sa.BigInteger(), nullable=False),
            sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
            sa.Column("classification", sa.String(length=12), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("body_text", _body(), nullable=True),
            sa.Column("body_html", _body(), nullable=True),
            sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.UniqueConstraint("uidvalidity", "imap_uid", name="uq_inbound_uid"),
            sa.ForeignKeyConstraint(
                ["sender_user_id"], ["users.id"], ondelete="SET NULL",
                name="fk_inbound_sender_user",
            ),
        )

    # Indexes are created OUTSIDE the table guard, each guarded on its own name.
    # Nested inside it, a crash between create_table and create_index left the
    # index missing forever: the rerun saw the table and skipped the whole block
    # (audit 2026-07-30).
    for name, cols in (
        ("ix_inbound_class_created", ["classification", "created_at"]),
        ("ix_inbound_status_created", ["status", "created_at"]),
    ):
        if not _has_index(bind, "inbound_messages", name):
            op.create_index(name, "inbound_messages", cols)

    if not _has_table(bind, "inbound_attachments"):
        op.create_table(
            "inbound_attachments",
            sa.Column("id", _big_int(), primary_key=True, autoincrement=True),
            sa.Column("message_id", _big_int(), nullable=False, index=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=127), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("av_state", sa.String(length=10), nullable=False),
            sa.ForeignKeyConstraint(
                ["message_id"], ["inbound_messages.id"], ondelete="CASCADE",
                name="fk_inbound_att_message",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "inbound_attachments"):
        op.drop_table("inbound_attachments")
    if _has_table(bind, "inbound_messages"):
        op.drop_table("inbound_messages")
