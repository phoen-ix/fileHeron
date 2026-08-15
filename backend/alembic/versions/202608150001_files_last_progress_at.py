"""files.last_progress_at - reap uploads on evidence of no progress.

cleanup_stale_uploads selected on `state == uploading AND created_at < cutoff`,
and `created_at` is stamped at /api/uploads/init, BEFORE the first byte moves.
Nothing refreshed the row for the duration of a transfer - tusd's enabled hooks
were pre-create, pre-finish, post-finish and post-terminate, none of which fire
during a PATCH. So the predicate measured elapsed wall-clock since the upload
STARTED, and every transfer slower than UPLOAD_STALE_AFTER_HOURS (3) was reaped
mid-flight, its share flipped to `failed` with reason "upload_abandoned" -
blaming the uploader for a server-side reap. At the 30 GB this product
advertises, 3 hours is ~23 Mbit/s sustained.

The obvious fix - read the tusd `.info` sidecar's mtime, as cleanup_abandoned_
uploads does - reproduces the bug: measured against the pinned tusproject/tusd
v2.9.2, the sidecar is written at creation and at finish only, so its mtime
tracks created_at, while the bare data file's mtime advances on every PATCH.

Rather than depend on tusd's on-disk layout, post-receive is now enabled and
stamps this column, so liveness is a fact on the row and is testable without a
filesystem fixture.

NULL for every existing row, and readers use COALESCE(last_progress_at,
created_at), so behaviour for rows written before this upgrade is exactly
today's. Uploads in flight ACROSS the upgrade are killed by the restart itself,
not by this.

Revision ID: 202608150001
Revises: 202608080001
Create Date: 2026-08-15
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202608150001"
down_revision = "202608080001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "files", "last_progress_at"):
        op.add_column(
            "files",
            sa.Column("last_progress_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "files", "last_progress_at"):
        op.drop_column("files", "last_progress_at")
