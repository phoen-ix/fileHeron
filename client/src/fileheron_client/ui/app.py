"""QApplication setup + light theme palette."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from ..assets_loader import asset_path


def build_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication(argv or [])
    app.setApplicationName("file:Heron")
    app.setOrganizationName("file:Heron")
    app.setStyle("Fusion")

    # Light palette matching the SPA's warm-paper scheme.
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#faf8f3"))
    pal.setColor(QPalette.WindowText, QColor("#1a1d24"))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f3efe5"))
    pal.setColor(QPalette.Text, QColor("#1a1d24"))
    pal.setColor(QPalette.Button, QColor("#f3efe5"))
    pal.setColor(QPalette.ButtonText, QColor("#1a1d24"))
    pal.setColor(QPalette.Highlight, QColor("#b45309"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, QColor("#b45309"))
    app.setPalette(pal)

    # Window icon — fall back to PNG if .ico missing in dev.
    for name in ("icon.ico", "icon.png", "heron.svg"):
        p = asset_path(name)
        if p.is_file():
            app.setWindowIcon(QIcon(str(p)))
            break

    return app
