"""Safe HTML sanitisation for admin-authored rich text (legal pages + email).

Content is now authored as HTML by the ProseMirror editor (no longer Markdown).
Because legal pages are world-readable and admin-authored, every stored/served
fragment is run through nh3 with a tight allowlist - the **single** source of
truth for "what HTML is allowed" across legal pages and email bodies.

Key safety properties:
- Only a fixed tag set (text, lists, links, images, tables, basic marks).
- ``class`` is allowed on any tag but its value is filtered down to the four
  alignment utilities only (``text-{left,center,right,justify}``) - so the
  editor's alignment survives while arbitrary/utility/JS-hook classes are
  dropped. No ``style`` attribute is ever allowed (no arbitrary CSS).
- ``a``/``img`` URLs restricted to http/https/mailto (no ``javascript:``/``data:``).
- ``rel="noopener noreferrer nofollow"`` forced on links.

``render_markdown_safe`` remains for the one-time Markdown->HTML migration of
pre-existing content (and any legacy caller); it renders CommonMark with raw
HTML disabled, then sanitises with the same allowlist.
"""
from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "breaks": True})

# The four alignment utility classes the editor emits (align attr -> class). The
# public legal CSS + the email CSS-inliner both key off exactly these names.
ALIGN_CLASSES = frozenset(
    {"text-left", "text-center", "text-right", "text-justify"}
)

_ALLOWED_TAGS = {
    # structure / text
    "p", "br", "hr", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code",
    # marks
    "strong", "b", "em", "i", "u", "s", "strike",
    # lists
    "ul", "ol", "li",
    # links + images
    "a", "img",
    # tables
    "table", "thead", "tbody", "tr", "th", "td",
}
# "*" = allowed on every tag (nh3 generic attributes); class value is then
# narrowed by _attr_filter to the alignment set.
_ALLOWED_ATTRS = {
    "*": {"class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
_ALLOWED_SCHEMES = {"http", "https", "mailto"}


def _attr_filter(tag: str, attr: str, value: str) -> str | None:
    """nh3 per-attribute rewrite. Keep only alignment classes; keep img
    width/height only if a plain integer; pass everything else that already
    survived the allowlist (href/src schemes are enforced by url_schemes)."""
    if attr == "class":
        kept = [c for c in value.split() if c in ALIGN_CLASSES]
        return " ".join(kept) if kept else None
    if attr in ("width", "height") and tag == "img":
        return value if value.isdigit() else None
    return value


def sanitize_html(html: str | None) -> str:
    """Sanitise an admin-authored HTML fragment to the safe allowlist. Empty/
    None -> empty string."""
    if not html or not html.strip():
        return ""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        attribute_filter=_attr_filter,
        url_schemes=_ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )


def render_markdown_safe(markdown: str | None) -> str:
    """Render CommonMark (raw HTML off) to sanitised HTML. Retained for the
    one-time Markdown->HTML migration of pre-existing legal/email content."""
    if not markdown or not markdown.strip():
        return ""
    return sanitize_html(_md.render(markdown))
