"""services/image.py::to_client_png - transcode + downscale to PNG."""
from __future__ import annotations

import io

from PIL import Image

from app.services.image import to_client_png


def _img_bytes(w: int, h: int, fmt: str = "PNG", color="red") -> bytes:
    out = io.BytesIO()
    mode = "RGB"
    Image.new(mode, (w, h), color).save(out, fmt)
    return out.getvalue()


def test_returns_png_magic():
    out = to_client_png(_img_bytes(100, 40))
    assert out.startswith(b"\x89PNG\r\n\x1a\n")


def test_downscales_tall_image():
    out = to_client_png(_img_bytes(400, 200), max_height=48)
    im = Image.open(io.BytesIO(out))
    assert im.height == 48
    assert im.width == 96  # aspect preserved (400:200 -> 96:48)


def test_does_not_upscale_small_image():
    out = to_client_png(_img_bytes(30, 20), max_height=48)
    im = Image.open(io.BytesIO(out))
    assert im.height == 20  # left as-is, never enlarged


def test_transcodes_jpeg_to_png():
    out = to_client_png(_img_bytes(80, 30, fmt="JPEG"))
    im = Image.open(io.BytesIO(out))
    assert im.format == "PNG"
