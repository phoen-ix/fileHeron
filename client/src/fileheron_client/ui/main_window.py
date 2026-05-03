"""Tabbed main window — Inbox · Outbox · New share."""
from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..api import ApiClient
from ..models import MeResponse
from .settings_dialog import SettingsDialog
from .share_detail_dialog import ShareDetailDialog
from .share_list_panel import ShareListPanel
from .upload_panel import UploadPanel


class MainWindow(QMainWindow):
    def __init__(self, api: ApiClient, me: MeResponse) -> None:
        super().__init__()
        self._api = api
        self._me = me
        self.setWindowTitle(f"file:Heron — {me.display_name} ({me.role})")
        self.resize(1000, 640)
        self._build_menu()
        self._build_central()

    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        a_settings = QAction("&Settings…", self)
        a_settings.triggered.connect(self._open_settings)
        m_file.addAction(a_settings)
        m_file.addSeparator()
        a_quit = QAction("&Quit", self)
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)

    def _build_central(self) -> None:
        tabs = QTabWidget()
        self.inbox = ShareListPanel(self._api, box="inbox")
        self.outbox = ShareListPanel(self._api, box="outbox")
        self.upload = UploadPanel(self._api)
        tabs.addTab(self.inbox, "Inbox")
        tabs.addTab(self.outbox, "Outbox")
        tabs.addTab(self.upload, "New share")
        tabs.currentChanged.connect(self._on_tab_changed)
        self.inbox.open_share.connect(self._open_share)
        self.outbox.open_share.connect(self._open_share)
        self.setCentralWidget(tabs)

    @Slot(int)
    def _on_tab_changed(self, idx: int) -> None:
        # Refresh list panels when revisited so newly-created/expired
        # shares show up without a manual click.
        if idx == 0:
            self.inbox.refresh()
        elif idx == 1:
            self.outbox.refresh()

    @Slot(str)
    def _open_share(self, share_id: str) -> None:
        dlg = ShareDetailDialog(self._api, share_id, parent=self)
        dlg.exec()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._api, self._me, parent=self)
        dlg.signed_out.connect(self.close)
        dlg.exec()
