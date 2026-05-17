"""Inbox / Outbox table panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..formatters import format_expiry
from ..models import ShareListItem
from .widgets import PillLabel, human_size


COLS = ("Subject", "Party", "Files", "Size", "Created", "Expires", "State")


class ShareListPanel(QWidget):
    """Used twice — for the Inbox tab (box=inbox, shows sender) and
    the Outbox tab (box=outbox, shows recipients)."""

    open_share = Signal(str)  # share_id

    def __init__(self, api: ApiClient, *, box: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._api = api
        self._box = box
        self._items: list[ShareListItem] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)

        # Filters row.
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search subject…")
        self.search.returnPressed.connect(self.refresh)
        row.addWidget(self.search, 1)

        self.state_filter = QComboBox()
        for label, value in [
            ("Active", "active"),
            ("Any state", ""),
            ("Expired", "expired"),
            ("Revoked", "revoked"),
            ("Deleted", "deleted"),
        ]:
            self.state_filter.addItem(label, userData=value)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.state_filter)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.refresh_btn)

        outer.addLayout(row)

        # Table.
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)  # Subject
        for i in range(1, len(COLS)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.cellDoubleClicked.connect(
            lambda row, _col: self._open_row(row)
        )
        outer.addWidget(self.table, 1)

        self.status = QLabel("")
        outer.addWidget(self.status)

    def refresh(self) -> None:
        states_value = self.state_filter.currentData()
        states = [states_value] if states_value else None
        self.status.setText("Loading…")
        try:
            resp = api_pkg.list_shares(
                self._api,
                box=self._box,
                q=self.search.text().strip(),
                states=states,
                page=1,
                page_size=200,
            )
        except ApiError as exc:
            self.status.setText(f"Error: {exc.message}")
            return
        self._items = resp.items
        self.status.setText(f"{len(resp.items)} of {resp.total} shares")
        self._render()

    def _render(self) -> None:
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            subject = item.effective_subject or "(no subject)"
            self.table.setItem(row, 0, QTableWidgetItem(subject))

            if self._box == "inbox":
                party = item.sender.display_name if item.sender else "(unknown)"
            else:
                party = ", ".join(r.label for r in item.recipients) or "(none)"
            self.table.setItem(row, 1, QTableWidgetItem(party))

            self.table.setItem(row, 2, QTableWidgetItem(str(item.file_count)))
            self.table.setItem(row, 3, QTableWidgetItem(human_size(item.total_size_bytes)))
            self.table.setItem(
                row, 4, QTableWidgetItem(item.created_at.strftime("%Y-%m-%d %H:%M"))
            )
            self.table.setItem(
                row, 5, QTableWidgetItem(format_expiry(item.expires_at))
            )
            self.table.setCellWidget(row, 6, PillLabel(item.state, state=item.state))

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._items):
            self.open_share.emit(self._items[row].id)
