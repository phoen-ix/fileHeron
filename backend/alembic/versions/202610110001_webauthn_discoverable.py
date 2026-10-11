"""passkey sign-in - whether a passkey was stored on the device.

`user_webauthn_credentials.discoverable`: the browser's `credProps.rk` report
at registration. A discoverable credential is one the authenticator keeps on
the device, the only kind that can sign in with no email and no password
(passkey sign-in, v2.27.0). Nullable and inert: NULL for every existing
passkey, and the column drives a UI badge only - the sign-in ceremony itself is
the proof.

Downgrade drops the column; the passkeys keep working as before.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202610110001
Revises: 202610100001
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202610110001"
down_revision = "202610100001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "user_webauthn_credentials", "discoverable"):
        op.add_column(
            "user_webauthn_credentials", sa.Column("discoverable", sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "user_webauthn_credentials", "discoverable"):
        op.drop_column("user_webauthn_credentials", "discoverable")
