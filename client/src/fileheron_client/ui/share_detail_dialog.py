"""Share detail — files + per-file download."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..formatters import format_expiry
from ..models import FileInShareResponse, MeResponse, ShareResponse
from .expiry_dialog import ExpiryDialog
from .widgets import PillLabel, human_size


class _DownloadWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(str)  # local path
    failed = Signal(str)

    def __init__(self, api: ApiClient, file_id: str, dest: Path) -> None:
        super().__init__()
        self.api = api
        self.file_id = file_id
        self.dest = dest

    def run(self) -> None:
        try:
            api_pkg.download_file(
                self.api,
                self.file_id,
                dest=self.dest,
                on_progress=lambda d, t: self.progress.emit(d, t),
            )
        except ApiError as exc:
            self.failed.emit(exc.message or str(exc))
        except Exception as exc:  # network failure
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit(str(self.dest))


class ShareDetailDialog(QDialog):
    # Emitted after the user mutates the share (revoke / expire-now /
    # edit-expiry) so the parent list view can re-fetch and reflect
    # the new state without a manual reload.
    share_mutated = Signal()

    def __init__(
        self,
        api: ApiClient,
        share_id: str,
        me: MeResponse,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._share_id = share_id
        self._me = me
        self._share: Optional[ShareResponse] = None
        self._workers: list[_DownloadWorker] = []
        self.setWindowTitle("Share")
        self.setMinimumSize(620, 460)
        self._build()
        self._load()

    def _can_manage(self) -> bool:
        """The Revoke / Expire-now / Edit-expiry buttons are visible only
        when the actor owns the share or is an admin. Mirrors the
        backend's `update_share_expiry` + `revoke_share` authorisation."""
        if self._share is None:
            return False
        return (
            self._me.role == "admin"
            or self._share.created_by_id == self._me.id
        )

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.title = QLabel("…")
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        outer.addWidget(self.title)
        self.meta = QLabel("")
        self.meta.setStyleSheet("color: #555;")
        outer.addWidget(self.meta)
        self.state = PillLabel("…")
        outer.addWidget(self.state, alignment=Qt.AlignLeft)

        outer.addWidget(QLabel("Files"))
        self.file_list = QListWidget()
        outer.addWidget(self.file_list, 1)

        btns = QHBoxLayout()
        # Manager actions (left-aligned). Created hidden; _load reveals
        # them when _can_manage() is True. Disabled when share state
        # isn't 'active' (server would reject anyway with SHARE_NOT_ACTIVE).
        self.edit_expiry_btn = QPushButton("Edit expiry…")
        self.edit_expiry_btn.clicked.connect(self._edit_expiry)
        self.edit_expiry_btn.hide()
        btns.addWidget(self.edit_expiry_btn)

        self.expire_now_btn = QPushButton("Expire now")
        self.expire_now_btn.clicked.connect(self._expire_now)
        self.expire_now_btn.hide()
        btns.addWidget(self.expire_now_btn)

        self.revoke_btn = QPushButton("Revoke")
        self.revoke_btn.clicked.connect(self._revoke)
        self.revoke_btn.hide()
        btns.addWidget(self.revoke_btn)

        btns.addStretch()
        self.save_all_btn = QPushButton("Save all to folder…")
        self.save_all_btn.clicked.connect(self._save_all)
        btns.addWidget(self.save_all_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btns.addWidget(self.close_btn)
        outer.addLayout(btns)

        self.progress = QProgressBar()
        self.progress.hide()
        outer.addWidget(self.progress)

    def _load(self) -> None:
        try:
            self._share = api_pkg.get_share(self._api, self._share_id)
        except ApiError as exc:
            QMessageBox.warning(self, "Could not load share", exc.message)
            self.reject()
            return
        s = self._share
        self.title.setText(s.effective_subject or "(no subject)")
        bits = [
            f"Created {s.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Expires {format_expiry(s.expires_at)}",
        ]
        if s.message:
            bits.append(s.message)
        self.meta.setText(" · ".join(bits))
        self.state.setState(s.state)
        self.state.setText(s.state)
        self._render_files(s.files)
        self._refresh_action_visibility()

    def _refresh_action_visibility(self) -> None:
        """Show + enable the manager-action buttons per role + state.
        Called from _load() and after any successful mutation so the
        UI reflects the current state without a full rebuild."""
        manage = self._can_manage()
        active = self._share is not None and self._share.state == "active"
        for btn in (self.edit_expiry_btn, self.expire_now_btn, self.revoke_btn):
            btn.setVisible(manage)
            btn.setEnabled(manage and active)

    # ---- Manager actions ----------------------------------------------------

    def _revoke(self) -> None:
        if not self._share:
            return
        if QMessageBox.question(
            self,
            "Revoke share",
            f"Revoke this share? Files become inaccessible to the recipient.\n\n"
            f"Subject: {self._share.effective_subject or '(no subject)'}",
        ) != QMessageBox.Yes:
            return
        try:
            api_pkg.revoke_share(self._api, self._share.id)
        except ApiError as exc:
            QMessageBox.warning(self, "Revoke failed", exc.message or str(exc))
            return
        self.share_mutated.emit()
        QMessageBox.information(self, "Revoked", "Share revoked.")
        self.accept()

    def _expire_now(self) -> None:
        if not self._share:
            return
        if QMessageBox.question(
            self,
            "Expire now",
            "Expire this share immediately? The file bytes are hard-"
            "deleted from disk; this cannot be undone.",
        ) != QMessageBox.Yes:
            return
        try:
            updated = api_pkg.expire_share_now(self._api, self._share.id)
        except ApiError as exc:
            QMessageBox.warning(self, "Expire failed", exc.message or str(exc))
            return
        self._share = updated
        self.share_mutated.emit()
        # Re-render the meta line + state pill + buttons rather than
        # closing the dialog, so the admin sees the new state.
        bits = [
            f"Created {updated.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Expires {format_expiry(updated.expires_at)}",
        ]
        if updated.message:
            bits.append(updated.message)
        self.meta.setText(" · ".join(bits))
        self.state.setState(updated.state)
        self.state.setText(updated.state)
        self._refresh_action_visibility()
        QMessageBox.information(self, "Expired", "Share expired.")

    def _edit_expiry(self) -> None:
        if not self._share:
            return
        dlg = ExpiryDialog(self, current=self._share.expires_at)
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.selected_expiry()
        if choice is None:
            return
        mode, value = choice
        try:
            if mode == "clear":
                updated = api_pkg.patch_share_expiry(
                    self._api, self._share.id, clear=True
                )
            else:
                updated = api_pkg.patch_share_expiry(
                    self._api, self._share.id, expires_at=value
                )
        except ApiError as exc:
            QMessageBox.warning(self, "Edit failed", exc.message or str(exc))
            return
        self._share = updated
        self.share_mutated.emit()
        bits = [
            f"Created {updated.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Expires {format_expiry(updated.expires_at)}",
        ]
        if updated.message:
            bits.append(updated.message)
        self.meta.setText(" · ".join(bits))
        QMessageBox.information(self, "Updated", "Share expiry updated.")

    def _render_files(self, files: list[FileInShareResponse]) -> None:
        self.file_list.clear()
        for f in files:
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(2, 2, 2, 2)
            row_l.addWidget(QLabel(f.original_filename), 1)
            row_l.addWidget(QLabel(human_size(f.size_bytes)))
            pill = PillLabel(f.state, state=f.state)
            row_l.addWidget(pill)
            dl = QPushButton("Download")
            dl.setEnabled(f.state in ("clean", "ready_unscanned"))
            dl.clicked.connect(lambda _checked=False, fid=f.id, fname=f.original_filename: self._download_one(fid, fname))
            row_l.addWidget(dl)
            item = QListWidgetItem(self.file_list)
            item.setSizeHint(row.sizeHint())
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, row)

    def _download_one(self, file_id: str, filename: str) -> None:
        dest_str, _filter = QFileDialog.getSaveFileName(
            self, "Save file as", filename
        )
        if not dest_str:
            return
        self._spawn_download(file_id, Path(dest_str))

    def _save_all(self) -> None:
        if not self._share:
            return
        downloadable = [f for f in self._share.files if f.state in ("clean", "ready_unscanned")]
        if not downloadable:
            QMessageBox.information(self, "Nothing to save", "No downloadable files.")
            return
        dir_str = QFileDialog.getExistingDirectory(self, "Save all to folder")
        if not dir_str:
            return
        base = Path(dir_str)
        for f in downloadable:
            self._spawn_download(f.id, base / f.original_filename)

    def _spawn_download(self, file_id: str, dest: Path) -> None:
        self.progress.show()
        self.progress.setValue(0)
        w = _DownloadWorker(self._api, file_id, dest)
        w.progress.connect(self._on_progress)
        w.finished_ok.connect(lambda p: self._on_dl_done(p, w))
        w.failed.connect(lambda m: self._on_dl_failed(m, w))
        self._workers.append(w)
        w.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _on_dl_done(self, path: str, w: _DownloadWorker) -> None:
        self._workers.remove(w)
        if not self._workers:
            self.progress.hide()
        QMessageBox.information(self, "Downloaded", f"Saved to:\n{path}")

    def _on_dl_failed(self, msg: str, w: _DownloadWorker) -> None:
        self._workers.remove(w)
        if not self._workers:
            self.progress.hide()
        QMessageBox.warning(self, "Download failed", msg)
