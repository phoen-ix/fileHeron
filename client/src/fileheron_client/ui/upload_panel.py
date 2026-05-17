"""New-share form: subject + recipients + expiry + (optional) public link + files."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..models import ShareResponse
from .recipient_picker import RecipientPickerWidget
from .upload_worker import UploadWorker
from .widgets import human_size

logger = logging.getLogger("fileheron_client.ui.upload")


class _DropList(QListWidget):
    """File list widget that accepts files dragged in from Explorer."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QDragEnterEvent) -> None:  # type: ignore[override]
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.is_file():
                    self.add_file(p)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def add_file(self, p: Path) -> None:
        existing = {self.item(i).data(Qt.UserRole) for i in range(self.count())}
        if str(p) in existing:
            return
        item = QListWidgetItem(f"{p.name}    [{human_size(p.stat().st_size)}]")
        item.setData(Qt.UserRole, str(p))
        self.addItem(item)

    def selected_paths(self) -> list[Path]:
        return [Path(self.item(i).data(Qt.UserRole)) for i in range(self.count())]


class UploadPanel(QWidget):
    def __init__(self, api: ApiClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._api = api
        self._workers: list[UploadWorker] = []
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        self.subject = QLineEdit()
        self.subject.setPlaceholderText("(optional — defaults to first filename)")
        form.addRow("Subject", self.subject)

        self.message = QTextEdit()
        self.message.setMaximumHeight(80)
        self.message.setPlaceholderText("Optional message to recipients")
        form.addRow("Message", self.message)

        # v0.3.0: structured recipient picker. Replaces the v0.2.x
        # free-text "user_ids: 1,2,3" input.
        self.recipients = RecipientPickerWidget(self._api)
        form.addRow("Recipients", self.recipients)

        # v0.3.0: explicit expiry picker with a Never checkbox.
        self._build_expiry_section(form)

        # v0.3.0: optional inline public-link block.
        self._build_public_link_section(form)

        # File picker.
        outer.addWidget(QLabel("Files (drag from Explorer or click Add)"))
        self.file_list = _DropList()
        outer.addWidget(self.file_list, 1)

        row = QHBoxLayout()
        self.add_btn = QPushButton("Add files…")
        self.add_btn.clicked.connect(self._on_add)
        row.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self._on_remove_selected)
        row.addWidget(self.remove_btn)
        row.addStretch()
        self.send_btn = QPushButton("Create share + upload")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._on_send)
        row.addWidget(self.send_btn)
        outer.addLayout(row)

        self.status = QLabel("")
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        outer.addWidget(self.progress)

    def _build_expiry_section(self, form: QFormLayout) -> None:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.expiry_picker = QDateTimeEdit(
            QDateTime.currentDateTime().addDays(7)
        )
        self.expiry_picker.setCalendarPopup(True)
        self.expiry_picker.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.expiry_picker.setMinimumDateTime(
            QDateTime.currentDateTime().addSecs(60)
        )
        v.addWidget(self.expiry_picker)
        self.expiry_never = QCheckBox("Never expires (revoke manually)")
        self.expiry_never.toggled.connect(
            lambda checked: self.expiry_picker.setEnabled(not checked)
        )
        v.addWidget(self.expiry_never)
        form.addRow("Expires", wrap)

    def _build_public_link_section(self, form: QFormLayout) -> None:
        # Visually grouped via a thin frame so the optional bundle reads
        # as one unit; greyed-out controls inside when the box is off.
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(frame)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        self.pl_enabled = QCheckBox("Include a public link")
        v.addWidget(self.pl_enabled)

        sub = QFormLayout()
        v.addLayout(sub)

        self.pl_password = QLineEdit()
        self.pl_password.setPlaceholderText("(optional)")
        self.pl_password.setEchoMode(QLineEdit.Password)
        sub.addRow("Password", self.pl_password)

        self.pl_download_limit = QSpinBox()
        self.pl_download_limit.setRange(0, 100_000)
        self.pl_download_limit.setSpecialValueText("Unlimited")
        sub.addRow("Download limit", self.pl_download_limit)

        self.pl_notify = QCheckBox("Notify me on every download")
        sub.addRow("", self.pl_notify)

        # Default to disabled; toggle wires enable across the block.
        for w in (self.pl_password, self.pl_download_limit, self.pl_notify):
            w.setEnabled(False)
        self.pl_enabled.toggled.connect(self._on_public_link_toggled)

        form.addRow("Public link", frame)

    def _on_public_link_toggled(self, checked: bool) -> None:
        for w in (self.pl_password, self.pl_download_limit, self.pl_notify):
            w.setEnabled(checked)

    def _on_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        for p in paths:
            self.file_list.add_file(Path(p))

    def _on_remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _collect_expiry(self) -> tuple[Optional[datetime], bool]:
        if self.expiry_never.isChecked():
            return None, True
        return self.expiry_picker.dateTime().toPython(), False

    def _collect_public_link(self) -> Optional[dict]:
        if not self.pl_enabled.isChecked():
            return None
        pw = self.pl_password.text().strip() or None
        dl = self.pl_download_limit.value()
        return {
            "password": pw,
            "download_limit": dl if dl > 0 else None,
            "notify_on_download": self.pl_notify.isChecked(),
        }

    def _on_send(self) -> None:
        files = self.file_list.selected_paths()
        if not files:
            QMessageBox.warning(self, "No files", "Add at least one file.")
            return
        public_link = self._collect_public_link()
        if not self.recipients.has_any() and public_link is None:
            QMessageBox.warning(
                self,
                "No recipients",
                "Add at least one user or group recipient, or attach an "
                "inline public link.",
            )
            return

        expires_at, never = self._collect_expiry()
        self.send_btn.setEnabled(False)
        self.status.setText("Creating share…")
        try:
            share: ShareResponse = api_pkg.create_share(
                self._api,
                kind="outbound",
                recipient_user_ids=self.recipients.user_ids(),
                recipient_group_ids=self.recipients.group_ids(),
                subject=self.subject.text().strip() or None,
                message=self.message.toPlainText().strip() or None,
                expires_at=expires_at if not never else None,
                expires_at_never=never,
                public_link=public_link,
            )
        except ApiError as exc:
            self.send_btn.setEnabled(True)
            self.status.setText(f"Error: {exc.message}")
            return

        # If the server minted a public link, show its URL ONCE — there's
        # no way to re-fetch the plaintext later.
        if public_link is not None:
            pl = getattr(share, "public_link", None)
            if pl is not None:
                url = pl.get("url") if isinstance(pl, dict) else getattr(pl, "url", None)
                if url:
                    QMessageBox.information(
                        self,
                        "Public link created",
                        f"Save this URL now — it will not be shown again.\n\n{url}",
                    )

        self.status.setText(
            f"Share {share.id[:8]} created — uploading {len(files)} file(s)…"
        )
        # Progress is driven by aggregate bytes across all queued workers
        # (v0.1.1 change) — smoother than per-file ticks.
        self.progress.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._completed = 0
        self._total_bytes = sum(p.stat().st_size for p in files)
        self._per_file_done: dict[str, int] = {}
        for p in files:
            w = UploadWorker(self._api, share.id, p)
            w.progress.connect(self._on_chunk_progress)
            w.finished_one.connect(self._on_one_done)
            w.failed.connect(self._on_one_failed)
            self._workers.append(w)
            w.start()

    def _on_chunk_progress(self, path: str, done: int, _total: int) -> None:
        self._per_file_done[path] = done
        if self._total_bytes <= 0:
            return
        done_total = sum(self._per_file_done.values())
        pct = max(0, min(100, int(done_total * 100 / self._total_bytes)))
        self.progress.setValue(pct)

    def _on_one_done(self, path: str, file_id: str) -> None:
        try:
            self._per_file_done[path] = Path(path).stat().st_size
        except OSError:
            pass
        self._completed += 1
        if self._completed == len(self._workers):
            self.progress.setValue(100)
            self._reset_form_after_send(success=True)
        else:
            self._on_chunk_progress(path, self._per_file_done.get(path, 0), 0)

    def _on_one_failed(self, path: str, message: str) -> None:
        QMessageBox.warning(self, "Upload failed", f"{Path(path).name}\n\n{message}")
        self._completed += 1
        if self._completed == len(self._workers):
            self._reset_form_after_send(success=False)

    def _reset_form_after_send(self, *, success: bool) -> None:
        self.send_btn.setEnabled(True)
        self.progress.hide()
        if success:
            self.status.setText("Share created and all files uploaded.")
            self.subject.clear()
            self.message.clear()
            self.recipients.reset()
            self.file_list.clear()
            self.pl_enabled.setChecked(False)
            self.pl_password.clear()
            self.pl_download_limit.setValue(0)
            self.pl_notify.setChecked(False)
        else:
            self.status.setText("Some uploads failed — see dialogs above.")
        self._workers.clear()
