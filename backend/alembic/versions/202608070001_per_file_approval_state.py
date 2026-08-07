"""Per-file four-eyes state + a durable "this share was gated" mark (v2.9.0).

Four-eyes was evaluated exactly once, in `create_share`, and never again. The
upload gate admits `active` as well as `pending_approval`, so an owner could get
a benign share approved and then upload the real payload into the now-live
share: it reached the recipients, and the public link served it, with no second
sign-off. Nothing re-triggered review, and no test covered the window.

The fix cannot live on `shares.state`. Reverting an active share to
`pending_approval` would be destructive rather than additive - both
`assert_share_downloadable` and `public_link.assert_link_usable` are
active-only, so appending one file would start 410-ing every existing recipient
and darken a live public link. So the mark goes on the FILE, and only the new
files wait.

`shares.approval_was_required` is what a later upload consults to know whether
the share it is landing on was gated. It is a stored fact rather than a live
re-evaluation of the policy: the policy is admin-tunable and its scope depends
on the recipient set, so re-asking `is_approval_required` at upload time would
answer for today's settings about a share approved under yesterday's.

Both columns default to the permissive value (`approved` / false), which is
exactly right for every pre-existing row: those shares either never went
through approval, or went through it under the old semantics where the whole
share was the unit of review. No backfill is possible or wanted - flagging
historical files as `pending_review` would freeze delivery on live shares.

Revision ID: 202608070001
Revises: 202607310001
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column, _has_index

revision = "202608070001"
down_revision = "202607310001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Each op guarded SEPARATELY: nesting the index inside the add_column guard
    # means a crash between the two skips the index forever on the retry
    # (tests/test_migration_reruns.py fails if a revision reintroduces that).
    if not _has_column(bind, "files", "approval_state"):
        op.add_column(
            "files",
            sa.Column(
                "approval_state",
                sa.String(length=20),
                nullable=False,
                server_default="approved",
            ),
        )
    if not _has_index(bind, "files", "ix_files_approval_state"):
        op.create_index("ix_files_approval_state", "files", ["approval_state"])
    if not _has_column(bind, "shares", "approval_was_required"):
        op.add_column(
            "shares",
            sa.Column(
                "approval_was_required",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "shares", "approval_was_required"):
        op.drop_column("shares", "approval_was_required")
    if _has_index(bind, "files", "ix_files_approval_state"):
        op.drop_index("ix_files_approval_state", table_name="files")
    if _has_column(bind, "files", "approval_state"):
        op.drop_column("files", "approval_state")
