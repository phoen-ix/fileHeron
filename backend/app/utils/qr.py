"""Server-side QR rendering. Returns inline-ready SVG XML for the 2FA setup
endpoint, so the TOTP secret never leaves the server in a way the frontend
bundle could leak. Uses the qrcode library's SvgPathImage factory.
"""
from __future__ import annotations

import io

import qrcode
import qrcode.image.svg


def render_qr_svg(data: str, *, box_size: int = 6, border: int = 2) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
