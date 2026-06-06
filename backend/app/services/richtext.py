"""Safe Markdown -> HTML rendering for admin-authored rich text (legal pages).

Mirrors the security flow used for email overrides in ``services/email.py``:
CommonMark with raw HTML disabled (so any literal ``<script>`` in the source is
escaped to text), then nh3 sanitisation as defense-in-depth with an allowlist
suited to legal documents (headings, lists, links, tables, basic emphasis).

Kept standalone (no DB, no email coupling) so any surface can render
admin-authored markdown safely.
"""
from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "breaks": True})

# Legal pages want a touch more than email bodies: full heading range + tables.
_ALLOWED_TAGS = {
    "p", "strong", "b", "em", "i", "u", "a", "ul", "ol", "li",
    "blockquote", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "hr", "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRS = {"a": {"href", "title"}}
_ALLOWED_SCHEMES = {"http", "https", "mailto"}


def render_markdown_safe(markdown: str | None) -> str:
    """Render `markdown` to sanitised HTML. Empty/None -> empty string."""
    if not markdown or not markdown.strip():
        return ""
    raw = _md.render(markdown)
    return nh3.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_SCHEMES,
    )
