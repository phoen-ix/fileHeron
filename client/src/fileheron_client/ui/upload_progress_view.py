"""Upload-progress screen (v0.9.x): a dedicated in-panel view shown after a
share is created. Replaces the create form (drill-down pack-swap, same shape
as ShareListPanel's detail drill-in) and shows:

  - a per-file progress bar (one bar per file, not one aggregate),
  - the one-time public link if the share minted one (copy + open),
  - a timestamped per-file activity log.

Uploads run here: ``start_uploads()`` kicks off one worker per file and the
``on_*`` callbacks (marshalled to the Tk main loop by upload_worker) drive the
matching row. When everything settles the screen STAYS and reveals the action
buttons — the form is restored only when the user picks "New share"
(``on_new_share``)."""
from __future__ import annotations

import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from ..api import ApiClient
from ..i18n import t
from ..models import ShareResponse
from .upload_worker import start_upload
from .widgets import alive, copy_to_clipboard_with_feedback, human_size

# (light, dark) text colours for the per-row + per-log terminal states.
_DONE_FG = ("#166534", "#bbf7d0")
_FAILED_FG = ("#991b1b", "#fecaca")


class UploadProgressView(ctk.CTkFrame):
    def __init__(
        self, master, root: ctk.CTk, api: ApiClient,
        share: ShareResponse, files: list[Path],
        *,
        on_new_share: Callable[[], None],
        on_view_outbox: Optional[Callable[[], None]] = None,
        flash: Optional[Callable[[str], None]] = None,
    ) -> None:
        # Never assign self._root — it shadows tkinter.Misc and breaks event
        # dispatch. Mirror the rest of the UI: keep the root on _app_root.
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._share = share
        self._files = files
        self._on_new_share = on_new_share
        self._on_view_outbox = on_view_outbox
        self._flash = flash
        # path_str -> {"bar": CTkProgressBar, "status": CTkLabel, "size": int}
        self._rows: dict[str, dict] = {}
        self._per_file_done: dict[str, int] = {}
        self._completed = 0
        self._failed_count = 0
        self._build()

    def _toast(self, text: str, kind: str = "info") -> None:
        if self._flash is not None:
            self._flash(text, kind=kind)
        else:
            try:
                self._status_var.set(text)
            except Exception:
                pass

    # ---- public-link extraction (mirrors upload_panel._on_created) -------

    def _extract_pl_url(self) -> Optional[str]:
        pl = getattr(self._share, "public_link", None)
        if isinstance(pl, dict):
            return pl.get("url")
        if pl is not None:
            return getattr(pl, "url", None)
        return None

    # ---- layout ---------------------------------------------------------

    def _build(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text=t("upload_progress.title"), anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        self._status_var = ctk.StringVar(
            value=t("upload.status_uploading",
                    short_id=self._share.id[:8], n=len(self._files)),
        )
        ctk.CTkLabel(outer, textvariable=self._status_var, anchor="w").pack(
            fill="x", pady=(2, 8),
        )

        url = self._extract_pl_url()
        if url:
            self._build_pl_card(outer, url)

        ctk.CTkLabel(outer, text=t("upload_progress.files_heading"), anchor="w").pack(fill="x")
        self._file_scroll = ctk.CTkScrollableFrame(
            outer, fg_color=("gray90", "gray20"), height=160,
        )
        self._file_scroll.pack(fill="both", expand=True, pady=(2, 8))
        self._file_scroll.grid_columnconfigure(0, weight=1)
        for r, p in enumerate(self._files):
            self._build_file_row(r, p)

        ctk.CTkLabel(outer, text=t("upload_progress.log_heading"), anchor="w").pack(fill="x")
        self._log = ctk.CTkTextbox(outer, height=120, state="disabled")
        self._log.pack(fill="x", pady=(2, 8))

        # Built now, revealed by _check_all_complete once nothing's in flight.
        self._actions = ctk.CTkFrame(outer, fg_color="transparent")
        ctk.CTkButton(
            self._actions, text=t("upload_progress.new_share_btn"),
            command=self._on_new_share,
        ).pack(side="left")
        if self._on_view_outbox is not None:
            ctk.CTkButton(
                self._actions, text=t("upload_progress.view_outbox_btn"),
                command=self._on_view_outbox, fg_color="gray",
            ).pack(side="left", padx=(8, 0))

    def _build_pl_card(self, parent, url: str) -> None:
        card = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")
        card.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(
            inner, text=t("upload.public_link_result_note"), anchor="w",
            text_color=_DONE_FG, wraplength=560,
        ).pack(fill="x", pady=(0, 6))
        url_row = ctk.CTkFrame(inner, fg_color="transparent")
        url_row.pack(fill="x")
        self._pl_url_var = ctk.StringVar(value=url)
        ctk.CTkEntry(url_row, textvariable=self._pl_url_var, state="readonly").pack(
            side="left", fill="x", expand=True,
        )
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_copy"), width=80, command=self._copy_url,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_open"), width=80, command=self._open_url,
        ).pack(side="left", padx=(4, 0))
        self._pl_copied_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            inner, textvariable=self._pl_copied_var, anchor="w", text_color=_DONE_FG,
        ).pack(fill="x", pady=(4, 0))

    def _build_file_row(self, r: int, p: Path) -> None:
        ps = str(p)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        row = ctk.CTkFrame(self._file_scroll, fg_color="transparent")
        row.grid(row=r, column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=p.name, anchor="w").grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            row, text=human_size(size), anchor="e", text_color="gray", width=80,
        ).grid(row=0, column=1, padx=(8, 8))
        bar = ctk.CTkProgressBar(row, width=160)
        bar.set(0)
        bar.grid(row=0, column=2, padx=(0, 8))
        status = ctk.CTkLabel(
            row, text=t("upload_progress.state_pending"), width=90, anchor="w",
        )
        status.grid(row=0, column=3, sticky="w")
        self._rows[ps] = {"bar": bar, "status": status, "size": size}
        self._per_file_done[ps] = 0

    # ---- public-link copy/open ------------------------------------------

    def _copy_url(self) -> None:
        copy_to_clipboard_with_feedback(
            self, self._pl_url_var.get(),
            feedback_var=self._pl_copied_var,
            on_fail=lambda: self._toast(t("share_detail.copy_failed_body"), kind="error"),
        )

    def _open_url(self) -> None:
        url = self._pl_url_var.get()
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # ---- log ------------------------------------------------------------

    def _log_event(self, msg: str) -> None:
        if not alive(self):
            return
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            self._log.configure(state="normal")
            self._log.insert("end", f"[{ts}] {msg}\n")
            self._log.see("end")
            self._log.configure(state="disabled")
        except Exception:
            pass

    # ---- upload orchestration -------------------------------------------

    def start_uploads(self) -> None:
        self._completed = 0
        self._failed_count = 0
        for p in self._files:
            self._log_event(t("upload_progress.log_started", name=p.name))
            start_upload(
                self._app_root, self._api,
                share_id=self._share.id,
                file_path=p,
                on_progress=self._on_chunk_progress,
                on_done=self._on_one_done,
                on_failed=self._on_one_failed,
            )

    def _on_chunk_progress(self, path: str, done: int, total: int) -> None:
        if not alive(self):
            return
        row = self._rows.get(path)
        if row is None:
            return
        self._per_file_done[path] = done
        denom = total if total > 0 else row["size"]
        if denom > 0:
            try:
                row["bar"].set(max(0.0, min(1.0, done / denom)))
            except Exception:
                pass
        try:
            row["status"].configure(text=t("upload_progress.state_uploading"))
        except Exception:
            pass

    def _on_one_done(self, path: str, _file_id: str) -> None:
        if not alive(self):
            return
        row = self._rows.get(path)
        if row is not None:
            try:
                row["bar"].set(1.0)
                row["status"].configure(
                    text=t("upload_progress.state_done"), text_color=_DONE_FG,
                )
            except Exception:
                pass
        self._log_event(t("upload_progress.log_done", name=Path(path).name))
        self._completed += 1
        self._check_all_complete()

    def _on_one_failed(self, path: str, message: str) -> None:
        if not alive(self):
            return
        row = self._rows.get(path)
        if row is not None:
            try:
                row["status"].configure(
                    text=t("upload_progress.state_failed"), text_color=_FAILED_FG,
                )
            except Exception:
                pass
        self._log_event(t("upload_progress.log_failed", name=Path(path).name, detail=message))
        self._toast(
            t("upload.upload_failed_toast", name=Path(path).name, detail=message),
            kind="error",
        )
        self._completed += 1
        self._failed_count += 1
        self._check_all_complete()

    def _check_all_complete(self) -> None:
        if not alive(self):
            return
        if self._completed < len(self._files):
            return
        if self._failed_count == 0:
            self._status_var.set(t("upload_progress.status_all_done", n=len(self._files)))
        else:
            self._status_var.set(
                t("upload_progress.status_partial",
                  failed=self._failed_count, total=len(self._files)),
            )
        try:
            self._actions.pack(fill="x", pady=(12, 0))
        except Exception:
            pass
