"""Resolve bundled asset paths in dev + PyInstaller onefile mode.

PyInstaller's onefile mode extracts the bundle to ``sys._MEIPASS`` at
runtime; in dev we live next to the source tree.
"""
from __future__ import annotations

import sys
from pathlib import Path


def asset_path(name: str) -> Path:
    """Return the absolute path to an asset under ``client/assets/``."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "assets" / name
    # Dev: client/src/fileheron_client/assets_loader.py → ../../assets/<name>
    return Path(__file__).resolve().parent.parent.parent / "assets" / name
