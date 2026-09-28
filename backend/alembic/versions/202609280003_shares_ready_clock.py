"""shares.expires_in_sec + shares.pending_added_notice - a share's clock and its
emails start when its files can be downloaded (v2.23.0).

`expires_in_sec`: a preset expiry counts from READY, not from creation. A 1-hour
share holding a 20 GB upload used to expire mid-transfer and fail the upload at
its last byte. While set, `expires_at` is NULL (the clock has not started).

`pending_added_notice`: the "files added" email waits until the added files have
been scanned and can be downloaded; this is the count owed.

`upload_batch_done`: the owner's client reported its batch complete, so the
post-scan trigger may announce at once instead of waiting out the quiet window.

NULL / false for every existing row - existing shares keep their fixed expiry,
owe no notice and are announced exactly as before. Each op guarded separately.

Revision ID: 202609280003
Revises: 202609280002
Create Date: 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202609280003"
down_revision = "202609280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "shares", "expires_in_sec"):
        op.add_column("shares", sa.Column("expires_in_sec", sa.Integer(), nullable=True))
    if not _has_column(bind, "shares", "pending_added_notice"):
        op.add_column(
            "shares", sa.Column("pending_added_notice", sa.Integer(), nullable=True)
        )
    if not _has_column(bind, "shares", "upload_batch_done"):
        op.add_column(
            "shares",
            sa.Column(
                "upload_batch_done", sa.Boolean(), nullable=False, server_default="0"
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "shares", "upload_batch_done"):
        op.drop_column("shares", "upload_batch_done")
    if _has_column(bind, "shares", "pending_added_notice"):
        op.drop_column("shares", "pending_added_notice")
    if _has_column(bind, "shares", "expires_in_sec"):
        op.drop_column("shares", "expires_in_sec")
