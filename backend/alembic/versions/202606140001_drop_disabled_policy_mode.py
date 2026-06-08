"""Drop the redundant ``disabled`` policy mode (api_token + public_link).

``disabled`` and ``admins_only`` were functionally identical in
``services/policy_gate.py::is_allowed`` (admin passes, otherwise allowlist-only),
so ``disabled`` was removed from the mode set. This migrates any deploy currently
storing ``disabled`` to its equivalent ``admins_only`` so the stored value matches
the surviving mode list. ``share_approval`` never had ``disabled`` and is untouched.

Re-runnable: a plain conditional UPDATE; running it again after the values are
already ``admins_only`` is a no-op.

Revision ID: 202606140001
Revises: 202606130001
Create Date: 2026-06-08
"""
from __future__ import annotations

from alembic import op

revision = "202606140001"
down_revision = "202606130001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE app_settings SET value = 'admins_only' "
        "WHERE key IN ('api_token.policy_mode', 'public_link.policy_mode') "
        "AND value = 'disabled'"
    )


def downgrade() -> None:
    # 'admins_only' is the correct equivalent of the removed mode; the original
    # 'disabled' value carried no extra meaning, so there is nothing to restore.
    pass
