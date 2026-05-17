"""New-share form: subject + recipients (free-text emails) + files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..models import ShareResponse
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

        self.recipients = QLineEdit()
        self.recipients.setPlaceholderText(
            "Recipient user IDs, comma-separated (recipient lookup by email is in v2)"
        )
        form.addRow("Recipient user IDs", self.recipients)

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

    def _on_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        for p in paths:
            self.file_list.add_file(Path(p))

    def _on_remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _parse_recipient_ids(self) -> list[int]:
        raw = self.recipients.text().strip()
        if not raw:
            return []
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                raise ValueError(f"'{part}' is not a numeric user id")
        return out

    def _on_send(self) -> None:
        files = self.file_list.selected_paths()
        if not files:
            QMessageBox.warning(self, "No files", "Add at least one file.")
            return
        try:
            user_ids = self._parse_recipient_ids()
        except ValueError as e:
            QMessageBox.warning(self, "Bad recipients", str(e))
            return
        if not user_ids:
            QMessageBox.warning(
                self,
                "No recipients",
                "Add at least one recipient user id (you can create a self-share by using your own id).",
            )
            return

        self.send_btn.setEnabled(False)
        self.status.setText("Creating share…")
        try:
            share: ShareResponse = api_pkg.create_share(
                self._api,
                kind="outbound",
                recipient_user_ids=user_ids,
                subject=self.subject.text().strip() or None,
                message=self.message.toPlainText().strip() or None,
            )
        except ApiError as exc:
            self.send_btn.setEnabled(True)
            self.status.setText(f"Error: {exc.message}")
            return

        self.status.setText(f"Share {share.id[:8]} created — uploading {len(files)} file(s)…")
        # Progress is driven by aggregate bytes across all queued
        # workers, not by file count — smoother for the common case of
        # 1-3 files where one big file dominates. Range is 0..100 (pct).
        self.progress.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._completed = 0
        # Per-worker progress bookkeeping. _per_file_done[path] tracks
        # the bytes already uploaded for that specific file; summed each
        # tick to derive the overall percent.
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
        # _total is per-file (provided by both the direct and TUS code
        # paths in upload_worker.py); we ignore it and use the upfront
        # stat()-based sum instead, so the percent is anchored to the
        # known total rather than per-file shifting.
        self._per_file_done[path] = done
        if self._total_bytes <= 0:
            return
        done_total = sum(self._per_file_done.values())
        pct = max(0, min(100, int(done_total * 100 / self._total_bytes)))
        self.progress.setValue(pct)

    def _on_one_done(self, path: str, file_id: str) -> None:
        # Snap the per-file counter to its final size in case the final
        # progress tick was elided (TUS sometimes emits the post-PATCH
        # state without an explicit "100% done" event).
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
            self.recipients.clear()
            self.file_list.clear()
        else:
            self.status.setText("Some uploads failed — see dialogs above.")
        self._workers.clear()
