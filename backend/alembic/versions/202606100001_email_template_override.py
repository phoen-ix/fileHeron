"""admin-editable email templates - email_template_override table (v1.25.0).

Stores per-(slug, locale) Markdown overrides for outbound email templates. The
renderer consults this table first and falls back to the built-in filesystem
templates when no row exists. Re-runnable: guarded by `_has_table`.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606100001"
down_revision = "202606090001"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "email_template_override"):
        return
    op.create_table(
        "email_template_override",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("slug", "locale", name="uq_email_tpl_slug_locale"),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_email_tpl_updated_by",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "email_template_override"):
        op.drop_table("email_template_override")
