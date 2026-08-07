"""users.sessions_invalidated_at - make "revoke sessions" cover access tokens.

Access JWTs carried only `{sub, iat, exp, jti, type}`; the `jti` was a
uniqueness nonce that was never persisted or consulted, and verification checked
signature/exp/type plus a live User lookup for existence and `is_disabled`.
Nothing session-scoped. So logout-others, password change, password reset, email
change, admin revoke-all and config-backup import all revoked the REFRESH row
and left a stolen access token working for its full TTL - 15 minutes by default,
but the TTL is admin-raisable to 1440, and nothing warned that "Session
revoked." became false for that long.

This column is the high-water mark. `jwt_session.revoke_all_user_refresh_tokens`
stamps it (one chokepoint covers all six paths) and
`resolve_user_from_access_token` refuses any token whose `iat` predates it - on
the User row it is already SELECTing for `is_disabled`, so no extra query, no
Redis, no denylist.

NULL for every existing row, which is correct: it means "nothing has been
revoked for this user yet", and no in-flight session is disturbed by the upgrade.

Revision ID: 202608070002
Revises: 202608070001
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202608070002"
down_revision = "202608070001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "sessions_invalidated_at"):
        op.add_column(
            "users",
            sa.Column("sessions_invalidated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "sessions_invalidated_at"):
        op.drop_column("users", "sessions_invalidated_at")
