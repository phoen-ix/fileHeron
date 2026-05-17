"""Share detail — files + per-file download + manager actions.

v0.4.0 CustomTkinter port. The dialog is a ``CTkToplevel`` with a
scrollable file list and three manager-action buttons (revoke /
expire-now / edit-expiry) gated by ownership."""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..formatters import format_expiry
from ..models import FileInShareResponse, MeResponse, ShareResponse
from ._async import run_in_background, run_with_progress
from . import _messagebox as mb
from .expiry_dialog import ExpiryDialog
from .widgets import PillLabel, human_size


class ShareDetailDialog:
    """Modal share-detail dialog. Caller passes ``on_mutated`` to be
    invoked after revoke / expire-now / edit-expiry succeeds, so the
    parent list view can re-fetch and reflect the new state."""

    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        share_id: str,
        me: MeResponse,
        *,
        on_mutated: Optional[Callable[[], None]] = None,
    ) -> None:
        self._app_root = root
        self._api = api
        self._share_id = share_id
        self._me = me
        self._on_mutated = on_mutated
        self._share: Optional[ShareResponse] = None
        self._dl_in_flight = 0

        self._win = ctk.CTkToplevel(root)
        self._win.title("Share")
        self._win.geometry("680x520")
        self._win.transient(root)

        self._build()
        self._load()

    def _can_manage(self) -> bool:
        if self._share is None:
            return False
        return (
            self._me.role == "admin"
            or self._share.created_by_id == self._me.id
        )

    def _build(self) -> None:
        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        self.title_var = ctk.StringVar(value="…")
        ctk.CTkLabel(
            outer, textvariable=self.title_var, anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x")

        self.meta_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            outer, textvariable=self.meta_var, anchor="w",
            justify="left", wraplength=620,
        ).pack(fill="x", pady=(2, 6))

        self.state_pill = PillLabel(outer, text="…", state="active")
        self.state_pill.pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(outer, text="Files", anchor="w").pack(fill="x")
        self.file_scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        self.file_scroll.pack(fill="both", expand=True, pady=(2, 8))

        # Actions row.
        btns = ctk.CTkFrame(outer, fg_color="transparent")
        btns.pack(fill="x")

        self.edit_expiry_btn = ctk.CTkButton(
            btns, text="Edit expiry…", command=self._edit_expiry, width=110,
        )
        self.expire_now_btn = ctk.CTkButton(
            btns, text="Expire now", command=self._expire_now, width=110,
        )
        self.revoke_btn = ctk.CTkButton(
            btns, text="Revoke", command=self._revoke, width=90,
            fg_color="#991b1b", hover_color="#7f1d1d",
        )
        # Initially hidden; _refresh_action_visibility shows them.
        # They pack on the LEFT (with stretch in between).
        self.edit_expiry_btn.pack(side="left", padx=(0, 4))
        self.expire_now_btn.pack(side="left", padx=4)
        self.revoke_btn.pack(side="left", padx=(4, 0))
        self.edit_expiry_btn.pack_forget()
        self.expire_now_btn.pack_forget()
        self.revoke_btn.pack_forget()

        # Right-aligned buttons.
        ctk.CTkButton(btns, text="Close", command=self._win.destroy, width=90, fg_color="gray").pack(side="right")
        ctk.CTkButton(
            btns, text="Save all to folder…", command=self._save_all, width=160,
        ).pack(side="right", padx=(0, 8))

        # Progress (download).
        self.progress = ctk.CTkProgressBar(outer)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.pack_forget()  # shown when a download is in flight

    def _load(self) -> None:
        def _fetch():
            return api_pkg.get_share(self._api, self._share_id)

        def _done(share):
            self._share = share
            self._render_after_load()

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self._win, "Could not load share", msg)
            self._win.destroy()

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _render_after_load(self) -> None:
        s = self._share
        if s is None:
            return
        self.title_var.set(s.effective_subject or "(no subject)")
        bits = [
            f"Created {s.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Expires {format_expiry(s.expires_at)}",
        ]
        if s.message:
            bits.append(s.message)
        self.meta_var.set(" · ".join(bits))
        self.state_pill.setState(s.state)
        self.state_pill.setText(s.state)
        self._render_files(s.files)
        self._refresh_action_visibility()

    def _refresh_action_visibility(self) -> None:
        manage = self._can_manage()
        active = self._share is not None and self._share.state == "active"
        for btn in (self.edit_expiry_btn, self.expire_now_btn, self.revoke_btn):
            if manage:
                btn.pack(side="left", padx=(0, 4)) if btn is self.edit_expiry_btn else btn.pack(side="left", padx=4)
            else:
                btn.pack_forget()
            btn.configure(state="normal" if (manage and active) else "disabled")

    # ---- Manager actions ----

    def _revoke(self) -> None:
        s = self._share
        if not s:
            return
        if not mb.confirm(
            self._win,
            "Revoke share",
            (
                f"Revoke this share? Files become inaccessible to the recipient.\n\n"
                f"Subject: {s.effective_subject or '(no subject)'}"
            ),
            ok_text="Revoke",
        ):
            return

        def _do():
            api_pkg.revoke_share(self._api, s.id)

        def _done(_result):
            if self._on_mutated is not None:
                self._on_mutated()
            mb.info(self._win, "Revoked", "Share revoked.")
            self._win.destroy()

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self._win, "Revoke failed", msg)

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    def _expire_now(self) -> None:
        s = self._share
        if not s:
            return
        if not mb.confirm(
            self._win,
            "Expire now",
            "Expire this share immediately? The file bytes are hard-"
            "deleted from disk; this cannot be undone.",
            ok_text="Expire",
        ):
            return

        def _do():
            return api_pkg.expire_share_now(self._api, s.id)

        def _done(updated):
            self._share = updated
            if self._on_mutated is not None:
                self._on_mutated()
            bits = [
                f"Created {updated.created_at.strftime('%Y-%m-%d %H:%M')}",
                f"Expires {format_expiry(updated.expires_at)}",
            ]
            if updated.message:
                bits.append(updated.message)
            self.meta_var.set(" · ".join(bits))
            self.state_pill.setState(updated.state)
            self.state_pill.setText(updated.state)
            self._refresh_action_visibility()
            mb.info(self._win, "Expired", "Share expired.")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self._win, "Expire failed", msg)

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    def _edit_expiry(self) -> None:
        s = self._share
        if not s:
            return
        dlg = ExpiryDialog(self._win, current=s.expires_at)
        choice = dlg.show_modal()
        if choice is None:
            return
        mode, value = choice

        def _do():
            if mode == "clear":
                return api_pkg.patch_share_expiry(self._api, s.id, clear=True)
            return api_pkg.patch_share_expiry(self._api, s.id, expires_at=value)

        def _done(updated):
            self._share = updated
            if self._on_mutated is not None:
                self._on_mutated()
            bits = [
                f"Created {updated.created_at.strftime('%Y-%m-%d %H:%M')}",
                f"Expires {format_expiry(updated.expires_at)}",
            ]
            if updated.message:
                bits.append(updated.message)
            self.meta_var.set(" · ".join(bits))
            mb.info(self._win, "Updated", "Share expiry updated.")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self._win, "Edit failed", msg)

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    # ---- Files ----

    def _render_files(self, files: list[FileInShareResponse]) -> None:
        for child in self.file_scroll.winfo_children():
            child.destroy()
        for r, f in enumerate(files):
            row = ctk.CTkFrame(self.file_scroll, fg_color="transparent")
            row.grid(row=r, column=0, sticky="ew", pady=2)
            self.file_scroll.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=f.original_filename, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=(0, 8)
            )
            ctk.CTkLabel(row, text=human_size(f.size_bytes), anchor="e").grid(
                row=0, column=1, padx=8
            )
            PillLabel(row, text=f.state, state=f.state).grid(row=0, column=2, padx=8)
            dl_btn = ctk.CTkButton(
                row, text="Download", width=90,
                command=lambda fid=f.id, fname=f.original_filename: self._download_one(fid, fname),
            )
            if f.state not in ("clean", "ready_unscanned"):
                dl_btn.configure(state="disabled")
            dl_btn.grid(row=0, column=3, padx=(8, 0))

    def _download_one(self, file_id: str, filename: str) -> None:
        dest_str = filedialog.asksaveasfilename(
            parent=self._win, title="Save file as", initialfile=filename,
        )
        if not dest_str:
            return
        self._spawn_download(file_id, Path(dest_str))

    def _save_all(self) -> None:
        if not self._share:
            return
        downloadable = [
            f for f in self._share.files
            if f.state in ("clean", "ready_unscanned")
        ]
        if not downloadable:
            mb.info(self._win, "Nothing to save", "No downloadable files.")
            return
        dir_str = filedialog.askdirectory(
            parent=self._win, title="Save all to folder"
        )
        if not dir_str:
            return
        base = Path(dir_str)
        for f in downloadable:
            self._spawn_download(f.id, base / f.original_filename)

    def _spawn_download(self, file_id: str, dest: Path) -> None:
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.set(0)
        self._dl_in_flight += 1

        def _do(tick):
            api_pkg.download_file(
                self._api, file_id, dest=dest, on_progress=tick,
            )
            return str(dest)

        def _on_progress(done, total):
            if total > 0:
                self.progress.set(min(1.0, done / total))

        def _done(path):
            self._dl_in_flight -= 1
            if self._dl_in_flight <= 0:
                self.progress.pack_forget()
            mb.info(self._win, "Downloaded", f"Saved to:\n{path}")

        def _failed(exc):
            self._dl_in_flight -= 1
            if self._dl_in_flight <= 0:
                self.progress.pack_forget()
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self._win, "Download failed", msg)

        run_with_progress(
            self._app_root, _do,
            on_progress=_on_progress,
            on_done=_done,
            on_failed=_failed,
        )

    def show_modal(self) -> None:
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))
        self._win.wait_window()
