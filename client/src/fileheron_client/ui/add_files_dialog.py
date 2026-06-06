"""Add files to an existing active share (v0.10.0).

A NON-blocking ``CTkToplevel`` (deliberately NOT a ``wait_window`` modal like
``ExpiryDialog``): the uploads run in background threads and marshal their
progress to the Tk main loop, so a blocking modal would freeze them. Mirrors
``UploadProgressView``'s per-file row + ``start_upload`` orchestration, adds a
file picker + a "notify recipients" checkbox, and on completion calls
``register_files_added`` (best-effort) so the share's recipients are re-notified
when asked. Owner + active is enforced server-side by the upload endpoints.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..i18n import t
from ._async import run_in_background
from .app import center_window
from .upload_worker import start_upload
from .widgets import alive, human_size

_DONE_FG = ("#166534", "#bbf7d0")
_FAILED_FG = ("#991b1b", "#fecaca")


class AddFilesDialog:
    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        share_id: str,
        subject: str,
        notify_default: bool,
        *,
        on_added: Callable[[], None],
        flash: Optional[Callable[[str], None]] = None,
    ) -> None:
        # Keep the root on _app_root - never assign self._root (it shadows
        # tkinter.Misc and breaks event dispatch on widget subclasses; harmless
        # here but we keep the convention).
        self._app_root = root
        self._api = api
        self._share_id = share_id
        self._on_added = on_added
        self._flash = flash
        # path_str -> {"path": Path, "size": int, "row", "bar", "status", "remove"}
        self._rows: dict[str, dict] = {}
        self._file_ids: list[str] = []
        self._completed = 0
        self._failed = 0
        self._total = 0
        self._uploading = False

        self._win = ctk.CTkToplevel(root)
        self._win.title(t("add_files.title"))
        center_window(self._win, 560, 470)
        self._win.transient(root)

        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        ctk.CTkLabel(
            outer,
            text=t("add_files.intro", subject=subject or t("share_list.no_subject")),
            anchor="w", wraplength=500, justify="left",
        ).pack(fill="x", pady=(0, 10))

        top_row = ctk.CTkFrame(outer, fg_color="transparent")
        top_row.pack(fill="x")
        self._add_btn = ctk.CTkButton(
            top_row, text=t("add_files.add_btn"), command=self._pick_files, width=140,
        )
        self._add_btn.pack(side="left")
        self._empty_var = ctk.StringVar(value=t("add_files.no_files"))
        ctk.CTkLabel(
            top_row, textvariable=self._empty_var, anchor="w", text_color="gray",
        ).pack(side="left", padx=(10, 0))

        self._scroll = ctk.CTkScrollableFrame(
            outer, fg_color=("gray90", "gray20"), height=200,
        )
        self._scroll.pack(fill="both", expand=True, pady=(8, 8))
        self._scroll.grid_columnconfigure(0, weight=1)

        self._notify_var = ctk.BooleanVar(value=notify_default)
        self._notify_cb = ctk.CTkCheckBox(
            outer, text=t("add_files.notify_label"), variable=self._notify_var,
        )
        self._notify_cb.pack(anchor="w", pady=(0, 8))

        self._status_var = ctk.StringVar(value="")
        ctk.CTkLabel(outer, textvariable=self._status_var, anchor="w").pack(
            fill="x", pady=(0, 8),
        )

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x")
        self._upload_btn = ctk.CTkButton(
            btn_row, text=t("add_files.upload_btn"), command=self._start, width=140,
        )
        self._upload_btn.pack(side="right")
        self._close_btn = ctk.CTkButton(
            btn_row, text=t("add_files.close_btn"), command=self._win.destroy,
            width=110, fg_color="gray",
        )
        self._close_btn.pack(side="right", padx=(0, 8))

        self._refresh_upload_enabled()
        self._win.bind(
            "<Escape>", lambda _e: None if self._uploading else self._win.destroy(),
        )
        # Modal focus WITHOUT wait_window - the uploads are async.
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))

    # ---- file list -------------------------------------------------------

    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self._win, title=t("add_files.pick_title"),
        )
        for p in paths:
            if str(p) not in self._rows:
                self._add_row(Path(p))
        self._empty_var.set("" if self._rows else t("add_files.no_files"))
        self._refresh_upload_enabled()

    def _add_row(self, path: Path) -> None:
        ps = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.grid(row=len(self._rows), column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=path.name, anchor="w").grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            row, text=human_size(size), anchor="e", text_color="gray", width=80,
        ).grid(row=0, column=1, padx=8)
        bar = ctk.CTkProgressBar(row, width=140)
        bar.set(0)
        status = ctk.CTkLabel(row, text=t("add_files.state_pending"), width=90, anchor="w")
        remove = ctk.CTkButton(
            row, text="✕", width=28, fg_color="transparent", border_width=1,
            hover_color=("gray85", "gray25"),
            command=lambda key=ps: self._remove_row(key),
        )
        remove.grid(row=0, column=2, padx=(8, 0))  # bar/status appear on upload
        self._rows[ps] = {
            "path": path, "size": size, "row": row,
            "bar": bar, "status": status, "remove": remove,
        }

    def _remove_row(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row is not None and alive(row["row"]):
            row["row"].destroy()
        self._empty_var.set("" if self._rows else t("add_files.no_files"))
        self._refresh_upload_enabled()

    def _refresh_upload_enabled(self) -> None:
        if alive(self._upload_btn):
            self._upload_btn.configure(
                state="normal" if (self._rows and not self._uploading) else "disabled",
            )

    # ---- upload ----------------------------------------------------------

    def _start(self) -> None:
        if self._uploading or not self._rows:
            return
        self._uploading = True
        self._completed = 0
        self._failed = 0
        self._file_ids = []
        self._total = len(self._rows)
        self._add_btn.configure(state="disabled")
        self._notify_cb.configure(state="disabled")
        self._close_btn.configure(state="disabled")
        self._upload_btn.configure(state="disabled")
        self._status_var.set(t("add_files.status_uploading", n=self._total))
        for row in self._rows.values():
            row["remove"].grid_remove()
            row["bar"].grid(row=0, column=2, padx=(0, 8))
            row["status"].grid(row=0, column=3, sticky="w")
            start_upload(
                self._app_root, self._api,
                share_id=self._share_id,
                file_path=row["path"],
                on_progress=self._on_progress,
                on_done=self._on_one_done,
                on_failed=self._on_one_failed,
            )

    def _on_progress(self, path: str, done: int, total: int) -> None:
        if not alive(self._win):
            return
        row = self._rows.get(path)
        if row is None:
            return
        denom = total if total > 0 else max(1, row["size"])
        try:
            row["bar"].set(max(0.0, min(1.0, done / denom)))
            row["status"].configure(text=t("add_files.state_uploading"))
        except Exception:
            pass

    def _on_one_done(self, path: str, file_id: str) -> None:
        if file_id:
            self._file_ids.append(file_id)
        row = self._rows.get(path)
        if row is not None and alive(row["bar"]):
            try:
                row["bar"].set(1.0)
                row["status"].configure(text=t("add_files.state_done"), text_color=_DONE_FG)
            except Exception:
                pass
        self._completed += 1
        self._maybe_finish()

    def _on_one_failed(self, path: str, _message: str) -> None:
        row = self._rows.get(path)
        if row is not None and alive(row["status"]):
            try:
                row["status"].configure(text=t("add_files.state_failed"), text_color=_FAILED_FG)
            except Exception:
                pass
        self._completed += 1
        self._failed += 1
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if self._completed < self._total:
            return
        if not self._file_ids:
            # Everything failed - re-enable so the user can retry.
            self._uploading = False
            for w in (self._add_btn, self._notify_cb, self._close_btn):
                if alive(w):
                    w.configure(state="normal")
            self._refresh_upload_enabled()
            if alive(self._win):
                self._status_var.set(t("add_files.status_all_failed"))
            return

        notify = bool(self._notify_var.get())
        file_ids = list(self._file_ids)

        def _do():
            return api_pkg.register_files_added(
                self._api, self._share_id, notify=notify, file_ids=file_ids,
            )

        def _finish(notified_ok: bool):
            # Files are uploaded + attached regardless; reload the detail view.
            self._on_added()
            if self._flash is not None:
                if notified_ok:
                    self._flash(t("add_files.toast_added", n=len(file_ids)), kind="success")
                else:
                    self._flash(t("add_files.notify_failed"), kind="error")
            if alive(self._win):
                self._win.destroy()

        # register_files_added failing (e.g. server < v1.12.0) must NOT lose the
        # upload - the files are already in the share. Degrade gracefully.
        run_in_background(
            self._app_root, _do,
            on_done=lambda _resp: _finish(True),
            on_failed=lambda _exc: _finish(False),
        )
