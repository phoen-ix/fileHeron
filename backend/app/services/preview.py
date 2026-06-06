"""In-browser preview policy (v1.23.0).

Serving user-uploaded bytes *inline* — so the browser renders them in place
rather than downloading — is the classic stored-XSS vector for a file host.
The defense lives here and is reused by every preview endpoint (authed +
public):

- A strict **allowlist** of types we will ever inline. It deliberately
  excludes ``image/svg+xml`` (SVG is an XML document that can carry
  ``<script>``) and never *renders* HTML: any ``text/*`` is served as
  ``text/plain`` source, so even ``text/html`` is shown verbatim, not executed.
- The stored ``File.mime_type`` is **client-supplied and untrusted** (set at
  upload, never re-derived). We map it through ``safe_content_type`` to a safe
  canonical value and pair it with ``X-Content-Type-Options: nosniff`` so the
  browser can't sniff a "png" into markup.

Callers MUST gate on ``is_previewable`` before serving and apply
``SECURITY_HEADERS`` to the inline response.

PDF trust model: a CSP does not govern JavaScript embedded *inside* a PDF — that
runs in the browser's PDF viewer, not the page. Modern viewers (Firefox's
PDF.js, Chrome's sandboxed PDFium) disable or sandbox PDF JS by default, and we
only ever preview AV-``clean`` files, so the residual risk is the browser
viewer's own sandbox plus up-to-date antivirus signatures. Admins who don't want
that surface at all can disable preview globally (``file_preview.enabled``).
"""
from __future__ import annotations

# Raster image types rendered inline via <img>. NO image/svg+xml.
PREVIEWABLE_IMAGE_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)
PDF_TYPE = "application/pdf"

# Hardening headers for every inline preview response (local backend). On the
# S3 backend the bytes are served by a presigned redirect and can't carry
# these — there the allowlist (never html/svg) is the sole defense.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
        "frame-ancestors 'self'"
    ),
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}


def _normalize(mime_type: str | None) -> str:
    """Lowercased bare type with any ``; charset=…`` parameter stripped."""
    if not mime_type:
        return ""
    return mime_type.split(";", 1)[0].strip().lower()


def preview_kind(mime_type: str | None) -> str | None:
    """Render strategy for ``mime_type`` — ``"image"`` | ``"pdf"`` | ``"text"``
    — or ``None`` when the type isn't previewable."""
    mime = _normalize(mime_type)
    if mime in PREVIEWABLE_IMAGE_TYPES:
        return "image"
    if mime == PDF_TYPE:
        return "pdf"
    if mime.startswith("text/"):
        return "text"
    return None


def is_previewable(mime_type: str | None) -> bool:
    return preview_kind(mime_type) is not None


def safe_content_type(mime_type: str | None) -> str:
    """The Content-Type we actually serve inline — a safe canonical value, NOT
    the untrusted stored type. Text of any flavour (incl. ``text/html``) is
    pinned to ``text/plain`` so it renders as source, never as a document."""
    kind = preview_kind(mime_type)
    if kind == "text":
        return "text/plain; charset=utf-8"
    if kind == "image":
        return _normalize(mime_type)
    if kind == "pdf":
        return PDF_TYPE
    # Caller is expected to gate on is_previewable() first; defensive fallback.
    return "application/octet-stream"
