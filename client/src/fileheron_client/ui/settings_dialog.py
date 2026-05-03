"""Settings dialog: server URL display, current account, sign-out."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..api import ApiClient
from ..models import MeResponse


class SettingsDialog(QDialog):
    signed_out = Signal()

    def __init__(
        self,
        api: ApiClient,
        me: MeResponse,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._me = me
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        form.addRow("Server", QLabel(self._api.server_url))
        form.addRow("Account", QLabel(f"{self._me.display_name}  ({self._me.email})"))
        form.addRow("Role", QLabel(self._me.role))
        form.addRow("Client version", QLabel(__version__))

        btns = QHBoxLayout()
        btns.addStretch()
        out_btn = QPushButton("Sign out")
        out_btn.clicked.connect(self._on_sign_out)
        btns.addWidget(out_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        outer.addLayout(btns)

    def _on_sign_out(self) -> None:
        from .. import api as api_pkg
        from ..config import clear_secret

        try:
            api_pkg.logout(self._api)
        except Exception:
            pass
        clear_secret("refresh", self._api.server_url)
        clear_secret("api_token", self._api.server_url)
        self.signed_out.emit()
        self.accept()
