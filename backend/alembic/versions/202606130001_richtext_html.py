"""Move admin rich-text from Markdown to HTML (legal pages + email templates).

The ProseMirror editor (v1.50) authors HTML. This:
  1. adds ``email_template_override.body_html`` and backfills it by rendering
     each existing ``body_markdown`` to HTML (raw - token hrefs like [RESET_URL]
     must survive; the email render path sanitises after URL substitution).
  2. converts the four legal kv values (legal.{imprint,privacy}_{en,de}) in
     ``app_settings`` from Markdown to HTML in place (the public legal endpoint
     sanitises on serve, so storing raw rendered HTML is safe).

Rendering uses CommonMark with raw-HTML disabled and link validation off (so a
token like ``[APP_URL]`` survives verbatim in an href). Re-runnable: the column
add is guarded, and re-rendering already-HTML content is a near no-op
(CommonMark passes HTML-looking text through as paragraphs).

Revision ID: 202606130001
Revises: 202606120001
Create Date: 2026-06-07
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202606130001"
down_revision = "202606120001"
branch_labels = None
depends_on = None

_LEGAL_KEYS = (
    "legal.imprint_en", "legal.imprint_de",
    "legal.privacy_en", "legal.privacy_de",
)


def _has_column(bind, table: str, column: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _renderer():
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "breaks": True})
    md.validateLink = lambda url: True
    md.normalizeLink = lambda url: url
    return md


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "email_template_override", "body_html"):
        op.add_column(
            "email_template_override",
            sa.Column("body_html", sa.Text(), nullable=True),
        )

    md = _renderer()

    # 1. Backfill email override HTML from Markdown (raw).
    rows = bind.execute(
        sa.text(
            "SELECT id, body_markdown FROM email_template_override "
            "WHERE body_html IS NULL"
        )
    ).fetchall()
    for row_id, body_md in rows:
        html = md.render(body_md or "")
        bind.execute(
            sa.text(
                "UPDATE email_template_override SET body_html = :h WHERE id = :i"
            ),
            {"h": html, "i": row_id},
        )

    # 2. Convert legal kv Markdown -> HTML in place (serve path sanitises).
    for key in _LEGAL_KEYS:
        r = bind.execute(
            sa.text("SELECT value FROM app_settings WHERE `key` = :k")
            if bind.dialect.name == "mysql"
            else sa.text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": key},
        ).fetchone()
        if r is None or not r[0] or not str(r[0]).strip():
            continue
        html = md.render(str(r[0]))
        bind.execute(
            sa.text("UPDATE app_settings SET value = :v WHERE `key` = :k")
            if bind.dialect.name == "mysql"
            else sa.text("UPDATE app_settings SET value = :v WHERE key = :k"),
            {"v": html, "k": key},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "email_template_override", "body_html"):
        op.drop_column("email_template_override", "body_html")
