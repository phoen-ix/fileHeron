"""Move admin rich-text from Markdown to HTML (legal pages + email templates).

The ProseMirror editor (v1.50) authors HTML. This:
  1. adds ``email_template_override.body_html`` and backfills it by rendering
     each existing ``body_markdown`` to HTML (raw - token hrefs like [RESET_URL]
     must survive; the email render path sanitises after URL substitution).
  2. converts the four legal kv values (legal.{imprint,privacy}_{en,de}) in
     ``app_settings`` from Markdown to HTML in place (the public legal endpoint
     sanitises on serve, so storing raw rendered HTML is safe).

Rendering uses CommonMark with raw-HTML disabled and link validation off (so a
token like ``[APP_URL]`` survives verbatim in an href).

Re-runnable, but NOT because re-rendering is harmless - it is the opposite.
``html: False`` makes markdown-it ESCAPE raw HTML, so a second pass turns
``<p>Hi</p>`` into ``&lt;p&gt;Hi&lt;/p&gt;`` and the imprint/privacy pages come
back as visible tag soup. (The docstring claimed the opposite until the
2026-07-30 audit.) Idempotency comes from a marker row plus a
looks-like-HTML sniff on each value, so a rerun after a partial failure
converts what is left and leaves what is done.

Revision ID: 202606130001
Revises: 202606120001
Create Date: 2026-06-07
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202606130001"
down_revision = "202606120001"
branch_labels = None
depends_on = None

_LEGAL_KEYS = (
    "legal.imprint_en", "legal.imprint_de",
    "legal.privacy_en", "legal.privacy_de",
)

# Written once the legal conversion has run. Its absence is what makes a
# reconverted-and-escaped page possible, so the sniff below backstops it.
_MARKER_KEY = "legal.richtext_migrated"

# Markdown never produces these at the start of a value; the previous run of
# this migration always does.
_HTML_PREFIXES = (
    "<p", "<h1", "<h2", "<h3", "<h4", "<h5", "<h6",
    "<ul", "<ol", "<blockquote", "<pre", "<div", "<table", "<hr",
)


def _looks_like_html(value: str) -> bool:
    return value.lstrip().lower().startswith(_HTML_PREFIXES)


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

    # 1. Backfill email override HTML from Markdown (raw).
    rows = bind.execute(
        sa.text(
            "SELECT id, body_markdown FROM email_template_override "
            "WHERE body_html IS NULL"
        )
    ).fetchall()
    md = _renderer() if rows else None
    for row_id, body_md in rows:
        html = md.render(body_md or "")
        bind.execute(
            sa.text(
                "UPDATE email_template_override SET body_html = :h WHERE id = :i"
            ),
            {"h": html, "i": row_id},
        )

    # 2. Convert legal kv Markdown -> HTML in place (serve path sanitises).
    select_marker = (
        sa.text("SELECT value FROM app_settings WHERE `key` = :k")
        if bind.dialect.name == "mysql"
        else sa.text("SELECT value FROM app_settings WHERE key = :k")
    )
    if bind.execute(select_marker, {"k": _MARKER_KEY}).fetchone() is not None:
        return

    for key in _LEGAL_KEYS:
        r = bind.execute(
            sa.text("SELECT value FROM app_settings WHERE `key` = :k")
            if bind.dialect.name == "mysql"
            else sa.text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": key},
        ).fetchone()
        if r is None or not r[0] or not str(r[0]).strip():
            continue
        if _looks_like_html(str(r[0])):
            continue  # already converted by an earlier (partial) run
        if md is None:
            md = _renderer()
        html = md.render(str(r[0]))
        bind.execute(
            sa.text("UPDATE app_settings SET value = :v WHERE `key` = :k")
            if bind.dialect.name == "mysql"
            else sa.text("UPDATE app_settings SET value = :v WHERE key = :k"),
            {"v": html, "k": key},
        )

    bind.execute(
        sa.text(
            "INSERT INTO app_settings (`key`, value, is_encrypted, updated_at) "
            "VALUES (:k, '1', 0, :t)"
        )
        if bind.dialect.name == "mysql"
        else sa.text(
            "INSERT INTO app_settings (key, value, is_encrypted, updated_at) "
            "VALUES (:k, '1', 0, :t)"
        ),
        {"k": _MARKER_KEY, "t": datetime.now(timezone.utc).replace(tzinfo=None)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "email_template_override", "body_html"):
        op.drop_column("email_template_override", "body_html")
