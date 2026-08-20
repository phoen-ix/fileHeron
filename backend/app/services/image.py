"""Small image transforms for branding.

Kept isolated so Pillow is only imported when actually transforming an image
(logo upload), never at app boot.
"""
from __future__ import annotations

import io

# Largest decoded image we will materialise. 25 MP at 4 bytes per RGBA pixel is
# already ~100 MB transient; see the note in to_client_png about why this is
# enforced by hand rather than left to Pillow's own threshold.
_MAX_PIXELS = 25_000_000


def to_client_png(data: bytes, *, max_height: int = 48) -> bytes:
    """Transcode an uploaded logo (PNG/JPEG/WebP) into a header-sized PNG for
    the desktop client, which renders with ``tkinter.PhotoImage`` (PNG-only, no
    smooth resize). Downscales to ``max_height`` preserving aspect ratio; never
    upscales. Returns PNG bytes."""
    from PIL import Image  # local import: keep Pillow out of the boot path

    from ..middleware.errors import AppError

    # Bound the decoded pixel count so a decompression bomb (a tiny file that
    # declares enormous dimensions) can't exhaust memory on decode/convert. A
    # header logo is small; 25 MP (e.g. 5000x5000) is generous (audit L16).
    #
    # Pillow only WARNS between MAX_IMAGE_PIXELS and 2x it, and raises
    # DecompressionBombError only ABOVE 2x - so setting the cap to 25 MP
    # actually admitted 50 MP, which at 4 bytes per RGBA pixel is ~200 MB
    # transient in a 1 GB container. Verified against the installed Pillow: a
    # 1.8x image decodes with nothing but a warning. Check the declared
    # dimensions ourselves before decoding, so the number in the comment is the
    # number enforced (audit 2026-07-30).
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            declared = img.size[0] * img.size[1]
            if declared > _MAX_PIXELS:
                raise AppError(
                    400, "IMAGE_TOO_LARGE", "Logo image is too large to process."
                )
            rgba = img.convert("RGBA")  # forces a decode under the pixel cap
            if rgba.height > max_height:
                new_w = max(1, round(rgba.width * (max_height / rgba.height)))
                rgba = rgba.resize((new_w, max_height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            rgba.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Image.DecompressionBombError as e:
        raise AppError(400, "IMAGE_TOO_LARGE", "Logo image is too large to process.") from e
