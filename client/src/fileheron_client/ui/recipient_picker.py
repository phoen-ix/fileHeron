"""Recipient picker — v0.3.0.

Replaces the free-text "user_ids: 1,2,3" input in upload_panel with
two friendlier widgets:

- ``UserPickerDialog`` — type-to-search list against /api/users/search,
  multi-select with Ctrl/Shift, OK adds the highlighted rows.
- ``GroupPickerDialog`` — full list from /api/groups/recipient-targets,
  multi-select with Ctrl/Shift, OK adds the highlighted rows.
- ``RecipientPickerWidget`` — the inline thing the parent embeds: two
  rows ("Users: …" / "Groups: …") with an "Add…" button per row.
  Exposes ``user_ids() -> list[int]`` and ``group_ids() -> list[int]``
  for the create-share submit handler.

The widget keeps two parallel lists internally — the integer IDs (sent
to the server) and the human display strings (rendered as plain text).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError


class _MultiSelectListDialog(QDialog):
    """Shared scaffolding for the user + group pickers — search box,
    list widget, OK/Cancel. Subclasses override ``_reload`` to populate
    the list when the search box changes."""

    def __init__(self, parent: Optional[QWidget], title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 480)

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to filter…")
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        layout.addWidget(self.list_widget, 1)

        self.empty_notice = QLabel("")
        self.empty_notice.setStyleSheet("color: gray;")
        self.empty_notice.hide()
        layout.addWidget(self.empty_notice)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.Ok)
        self._ok_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Debounce typing so we only call the API after the user stops
        # typing for ~200ms — keeps the picker responsive without
        # hammering /search on every keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self) -> None:
        self._ok_btn.setEnabled(bool(self.list_widget.selectedItems()))

    def _reload(self) -> None:
        raise NotImplementedError


class UserPickerDialog(_MultiSelectListDialog):
    """Type-to-search picker against /api/users/search. Sends an
    initial empty-query load so the dialog isn't blank on open."""

    def __init__(self, api: ApiClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, "Add recipients (users)")
        self._api = api
        # Initial load (empty needle → full visible set, server-scoped
        # to the actor's role).
        self._reload()

    def _reload(self) -> None:
        q = self.search.text().strip()
        try:
            resp = api_pkg.search_users(self._api, q)
        except ApiError as exc:
            QMessageBox.warning(self, "Could not search users", exc.message)
            return
        self.list_widget.clear()
        for u in resp.items:
            item = QListWidgetItem(
                f"{u.display_name}  ·  {u.email}  ·  {u.role}"
            )
            item.setData(Qt.UserRole, u.user_id)
            self.list_widget.addItem(item)
        if not resp.items:
            self.empty_notice.setText(
                "No matches." if q else "No users available to address."
            )
            self.empty_notice.show()
        else:
            self.empty_notice.hide()


class GroupPickerDialog(_MultiSelectListDialog):
    """List picker for /api/groups/recipient-targets. The full list is
    short enough that we don't need server-side filtering — local
    substring match on the search box is fine."""

    def __init__(self, api: ApiClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, "Add recipients (groups)")
        self._api = api
        self._all_groups: list = []
        # One-shot fetch; subsequent searches filter the cached list.
        try:
            resp = api_pkg.list_recipient_groups(self._api)
            self._all_groups = resp.items
        except ApiError as exc:
            QMessageBox.warning(self, "Could not load groups", exc.message)
        self._reload()

    def _reload(self) -> None:
        needle = self.search.text().strip().lower()
        self.list_widget.clear()
        matched = [
            g
            for g in self._all_groups
            if not needle
            or needle in g.name.lower()
            or (g.description and needle in g.description.lower())
        ]
        for g in matched:
            badges = []
            if g.is_company_inbox:
                badges.append("inbox")
            badges.append(f"{g.member_count} member(s)")
            label = f"{g.name}  ·  {', '.join(badges)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, g.id)
            self.list_widget.addItem(item)
        if not matched:
            self.empty_notice.setText(
                "No matches." if needle else "No groups available to address."
            )
            self.empty_notice.show()
        else:
            self.empty_notice.hide()


class RecipientPickerWidget(QWidget):
    """Inline picker embedded in the share-create form. Tracks selected
    users + groups by id and renders them as plain comma-joined names."""

    def __init__(self, api: ApiClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._api = api
        self._user_ids: list[int] = []
        self._user_labels: list[str] = []
        self._group_ids: list[int] = []
        self._group_labels: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Users row
        users_row = QHBoxLayout()
        users_row.addWidget(QLabel("Users:"))
        self.users_summary = QLabel("(none)")
        self.users_summary.setWordWrap(True)
        self.users_summary.setStyleSheet("color: #333;")
        users_row.addWidget(self.users_summary, 1)
        add_user_btn = QPushButton("Add user…")
        add_user_btn.clicked.connect(self._add_users)
        users_row.addWidget(add_user_btn)
        clear_user_btn = QPushButton("Clear")
        clear_user_btn.clicked.connect(self._clear_users)
        users_row.addWidget(clear_user_btn)
        layout.addLayout(users_row)

        # Groups row
        groups_row = QHBoxLayout()
        groups_row.addWidget(QLabel("Groups:"))
        self.groups_summary = QLabel("(none)")
        self.groups_summary.setWordWrap(True)
        self.groups_summary.setStyleSheet("color: #333;")
        groups_row.addWidget(self.groups_summary, 1)
        add_group_btn = QPushButton("Add group…")
        add_group_btn.clicked.connect(self._add_groups)
        groups_row.addWidget(add_group_btn)
        clear_group_btn = QPushButton("Clear")
        clear_group_btn.clicked.connect(self._clear_groups)
        groups_row.addWidget(clear_group_btn)
        layout.addLayout(groups_row)

    # ---- public API --------------------------------------------------

    def user_ids(self) -> list[int]:
        return list(self._user_ids)

    def group_ids(self) -> list[int]:
        return list(self._group_ids)

    def has_any(self) -> bool:
        return bool(self._user_ids or self._group_ids)

    def reset(self) -> None:
        self._user_ids.clear()
        self._user_labels.clear()
        self._group_ids.clear()
        self._group_labels.clear()
        self._render()

    # ---- internal ----------------------------------------------------

    def _add_users(self) -> None:
        dlg = UserPickerDialog(self._api, self)
        if dlg.exec() != QDialog.Accepted:
            return
        for item in dlg.list_widget.selectedItems():
            uid = item.data(Qt.UserRole)
            if uid in self._user_ids:
                continue
            self._user_ids.append(uid)
            self._user_labels.append(item.text().split("  ·  ")[0])
        self._render()

    def _add_groups(self) -> None:
        dlg = GroupPickerDialog(self._api, self)
        if dlg.exec() != QDialog.Accepted:
            return
        for item in dlg.list_widget.selectedItems():
            gid = item.data(Qt.UserRole)
            if gid in self._group_ids:
                continue
            self._group_ids.append(gid)
            self._group_labels.append(item.text().split("  ·  ")[0])
        self._render()

    def _clear_users(self) -> None:
        self._user_ids.clear()
        self._user_labels.clear()
        self._render()

    def _clear_groups(self) -> None:
        self._group_ids.clear()
        self._group_labels.clear()
        self._render()

    def _render(self) -> None:
        self.users_summary.setText(
            ", ".join(self._user_labels) if self._user_labels else "(none)"
        )
        self.groups_summary.setText(
            ", ".join(self._group_labels) if self._group_labels else "(none)"
        )
