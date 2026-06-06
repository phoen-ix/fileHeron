"""Small image transforms for branding.

Kept isolated so Pillow is only imported when actually transforming an image
(logo upload), never at app boot.
"""
from __future__ import annotations

import io


def to_client_png(data: bytes, *, max_height: int = 48) -> bytes:
    """Transcode an uploaded logo (PNG/JPEG/WebP) into a header-sized PNG for
    the desktop client, which renders with ``tkinter.PhotoImage`` (PNG-only, no
    smooth resize). Downscales to ``max_height`` preserving aspect ratio; never
    upscales. Returns PNG bytes."""
    from PIL import Image  # local import: keep Pillow out of the boot path

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGBA")
        if img.height > max_height:
            new_w = max(1, round(img.width * (max_height / img.height)))
            img = img.resize((new_w, max_height), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
