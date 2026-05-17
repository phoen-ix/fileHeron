"""Small modal: pick a new expiry datetime, or check "Never" to clear.

Used by the share-detail dialog's Edit-expiry action (v0.2.0). The
caller reads ``selected_expiry()`` which returns one of:

- ``("set", datetime)`` — user picked a future datetime
- ``("clear", None)`` — user checked the Never box
- ``None`` — dialog was rejected (no-op)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)


class ExpiryDialog(QDialog):
    """Modal dialog for changing a share's expiry.

    `current` (optional): the share's existing expiry; pre-populates
    the date picker. If None, the picker defaults to now + 7 days
    (matches the SPA's default preset) and the Never box is checked.
    """

    def __init__(self, parent=None, current: Optional[datetime] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit expiry")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Set a new expiry datetime, or check Never."))

        # Date+time picker. Use the current expiry if set, else 7 days
        # out — same default as the SPA's ExpiryPicker.
        default = current if current is not None else datetime.utcnow() + timedelta(days=7)
        self.picker = QDateTimeEdit(QDateTime(default))
        self.picker.setCalendarPopup(True)
        self.picker.setDisplayFormat("yyyy-MM-dd HH:mm")
        # Block past dates so we don't have to validate after accept.
        self.picker.setMinimumDateTime(QDateTime.currentDateTime().addSecs(60))
        layout.addWidget(self.picker)

        self.never_box = QCheckBox("Never expires")
        self.never_box.setChecked(current is None)
        self.never_box.toggled.connect(self._on_never_toggled)
        layout.addWidget(self.never_box)

        self.help = QLabel(
            "When checked, the share is never auto-deleted by the cron. "
            "Revoke it manually when you're done."
        )
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: gray;")
        layout.addWidget(self.help)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Apply initial enabled/disabled state based on Never box.
        self._on_never_toggled(self.never_box.isChecked())

    def _on_never_toggled(self, checked: bool) -> None:
        # Greying out the picker rather than hiding it keeps the dialog
        # height stable when the box is toggled — easier on the eyes.
        self.picker.setEnabled(not checked)

    def selected_expiry(self) -> Optional[Tuple[str, Optional[datetime]]]:
        """Return the user's choice. Only meaningful after ``exec()``
        returns ``Accepted``. Use ``("clear", None)`` for never, or
        ``("set", datetime)`` for a real value."""
        if self.never_box.isChecked():
            return ("clear", None)
        qdt = self.picker.dateTime()
        # Convert QDateTime → naive Python datetime in local time. The
        # caller (api.patch_share_expiry) will turn this into an ISO
        # string; the backend treats it as naive UTC after the standard
        # offset-strip — same as the SPA's "local ISO sent as-is" flow.
        return ("set", qdt.toPython())
