"""Performance: compound indexes for hot query paths (v1.8.0).

Adds covering compound indexes that match the actual filter/sort shapes:
- shares (state, expires_at)               - hourly expire_files cron + list
- refresh_tokens (user_id, revoked_at, expires_at) - admin session list
- notifications (user_id, created_at)       - bell list + daily age-out cron
- login_attempts (ip, attempted_at) + (email, attempted_at) - rate-limit counts

All guarded by `_has_index` so the migration is re-runnable after a partial
failure. No data change.

Revision ID: 202606051300
Revises: 202606051200
Create Date: 2026-06-05
"""
from __future__ import annotations

from alembic import op
from app.db_guards import _has_index

revision = "202606051300"
down_revision = "202606051200"
branch_labels = None
depends_on = None


_INDEXES = [
    ("ix_shares_state_expires", "shares", ["state", "expires_at"]),
    (
        "ix_refresh_tokens_user_active",
        "refresh_tokens",
        ["user_id", "revoked_at", "expires_at"],
    ),
    ("ix_notifications_user_created", "notifications", ["user_id", "created_at"]),
    ("ix_login_attempts_ip_time", "login_attempts", ["ip", "attempted_at"]),
    ("ix_login_attempts_email_time", "login_attempts", ["email", "attempted_at"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, cols in _INDEXES:
        if not _has_index(bind, table, name):
            op.create_index(name, table, cols)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _cols in _INDEXES:
        if _has_index(bind, table, name):
            op.drop_index(name, table_name=table)
