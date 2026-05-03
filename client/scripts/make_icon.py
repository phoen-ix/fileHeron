"""One-off script to regenerate ``assets/icon.ico`` + ``assets/icon.png``
from ``assets/heron.svg``.

Not run during normal install or build; only when the source SVG
changes. Requires:

    pip install pillow cairosvg
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

try:
    import cairosvg
    from PIL import Image
except ImportError:
    print(
        "ERROR: install dev tools first: pip install pillow cairosvg",
        file=sys.stderr,
    )
    raise SystemExit(1)


HERE = Path(__file__).resolve().parent.parent
SVG = HERE / "assets" / "heron.svg"
ICO = HERE / "assets" / "icon.ico"
PNG = HERE / "assets" / "icon.png"

ICO_SIZES = (256, 128, 64, 48, 32, 16)


def main() -> int:
    if not SVG.is_file():
        print(f"missing source: {SVG}", file=sys.stderr)
        return 1

    # Render at the largest needed size, then downscale for the ICO.
    png_bytes = cairosvg.svg2png(
        url=str(SVG), output_width=512, output_height=512
    )
    big = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    # Save the 256-px PNG for Qt's window-icon fallback.
    big.resize((256, 256), Image.LANCZOS).save(PNG, "PNG")

    # Build the multi-size .ico for Windows.
    icons = [big.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    icons[0].save(
        ICO,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=icons[1:],
    )
    print(f"wrote {ICO} ({len(ICO_SIZES)} sizes) and {PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
