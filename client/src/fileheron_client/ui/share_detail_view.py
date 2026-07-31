"""Share detail - files + per-file download + manager actions.

v0.6.0 refactor: this used to be ``ShareDetailDialog`` - a separate
``CTkToplevel`` window with ``transient`` + ``grab_set``. Users
disliked the extra window. The class is now ``ShareDetailView``, a
``CTkFrame`` that packs into the parent ``ShareListPanel`` in place
of the list. The list panel handles the pack swap; the "← Back"
button at the top calls back into ``on_back`` to return to the list.

v0.9.4: informational notices (downloaded / ended / expiry-updated / …) now
flash a non-modal toast (via the ``flash`` callback) instead of an ``mb.info``
popup. Destructive confirms (``mb.confirm`` for End-share), error warnings
(``mb.warn``), the edit dialogs (``ExpiryDialog``/``LimitDialog``), and the
native ``filedialog`` calls stay as pop-ups."""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from .. import downloads_registry as dlreg
from ..api import ApiClient, DownloadCancelled, DownloadPaused
from ..api import download_checkpoint as dlckpt
from ..formatters import (
    RateEstimator,
    format_datetime,
    format_eta,
    format_expiry,
    format_rate,
)
from ..i18n import t
from ..models import FileInShareResponse, MeResponse, ShareResponse
from ..safe_path import safe_join
from ._async import run_in_background, run_with_progress
from . import _messagebox as mb
from .add_files_dialog import AddFilesDialog
from .expiry_dialog import ExpiryDialog
from .limit_dialog import LimitDialog
from .widgets import PillLabel, alive, copy_to_clipboard_with_feedback, human_size


class ShareDetailView(ctk.CTkFrame):
    """In-window share detail (v0.6.0+, was ``ShareDetailDialog``).

    Constructor arguments mirror the old dialog plus ``on_back`` - the
    drill-out callback the host ``ShareListPanel`` provides. Pack into
    the parent yourself; the view doesn't do its own geometry."""

    def __init__(
        self,
        master,
        root: ctk.CTk,
        api: ApiClient,
        share_id: str,
        me: MeResponse,
        *,
        on_back: Callable[[], None],
        on_mutated: Optional[Callable[[], None]] = None,
        flash: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._share_id = share_id
        self._me = me
        self._on_back = on_back
        self._on_mutated = on_mutated
        self._flash = flash
        self._share: Optional[ShareResponse] = None
        # file_id -> {"bar", "info_var", "info_lbl", "action_cell", "filename",
        #   "downloadable", "dest", "cancel", "pause", "last", "pause_btn"}
        self._file_rows: dict[str, dict] = {}

        self._build()
        self._load()
        # Esc-to-close parity with the old Toplevel modal. Bind on the
        # toplevel so it works regardless of focus inside the frame;
        # unbind when this view is destroyed so we don't leak the
        # binding into whatever replaces us.
        self._top = self.winfo_toplevel()
        self._esc_funcid = self._top.bind(
            "<Escape>", lambda _e: self._on_back(), add="+"
        )
        self.bind("<Destroy>", self._on_destroy_unbind, add="+")

    def _toast(self, text: str, kind: str = "info") -> None:
        """Non-modal success/info notice (replaces interrupting mb.info popups)."""
        if self._flash is not None:
            self._flash(text, kind=kind)

    def _on_destroy_unbind(self, _event) -> None:
        try:
            self._top.unbind("<Escape>", self._esc_funcid)
        except Exception:
            pass

    def _can_manage(self) -> bool:
        if self._share is None:
            return False
        return (
            self._me.role == "admin"
            or self._share.created_by_id == self._me.id
        )

    def _build(self) -> None:
        # Top header with "← Back" + a textual breadcrumb. Sits above
        # the existing content frame so it scrolls / resizes
        # independently of the file list.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkButton(
            header,
            text=t("share_detail.back"),
            width=110,
            height=28,
            fg_color="transparent",
            border_width=1,
            hover_color=("gray85", "gray25"),
            command=self._on_back,
        ).pack(side="left")

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        self.title_var = ctk.StringVar(value=t("share_detail.title_placeholder"))
        ctk.CTkLabel(
            outer, textvariable=self.title_var, anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x")

        self.meta_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            outer, textvariable=self.meta_var, anchor="w",
            justify="left", wraplength=620,
        ).pack(fill="x", pady=(2, 6))

        self.state_pill = PillLabel(outer, text=t("share_detail.title_placeholder"), state="active")
        self.state_pill.pack(anchor="w", pady=(0, 8))

        # Public-link section (only shown if the share has one;
        # populated by _load_public_link via background fetch).
        self._build_public_link_section(outer)

        ctk.CTkLabel(outer, text=t("share_detail.files_heading"), anchor="w").pack(fill="x")
        self.file_scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        self.file_scroll.pack(fill="both", expand=True, pady=(2, 8))

        # Actions row.
        btns = ctk.CTkFrame(outer, fg_color="transparent")
        btns.pack(fill="x")

        # v0.10.0: owner-only "Add files" - upload more files into this active
        # share (the upload endpoints are owner-only; admins can't, so this is
        # gated separately from the _can_manage() buttons below).
        self.add_files_btn = ctk.CTkButton(
            btns, text=t("share_detail.add_files_btn"),
            command=self._add_files, width=140,
        )
        self.edit_expiry_btn = ctk.CTkButton(
            btns, text=t("share_detail.edit_expiry_btn"),
            command=self._edit_expiry, width=140,
        )
        # v0.7.1: per-share download-budget editor (matches SPA).
        self.edit_limit_btn = ctk.CTkButton(
            btns, text=t("share_detail.edit_limit_btn"),
            command=self._edit_limit, width=140,
        )
        # v0.6.1: single destructive "End share" replaces the old
        # Revoke + Expire-now pair. Same backend call as before
        # (POST /api/shares/{id}/expire) - state → expired, files
        # hard-deleted. Red styling because this is now the only
        # destructive manager action.
        self.end_share_btn = ctk.CTkButton(
            btns, text=t("share_detail.end_share_btn"),
            command=self._end_share, width=140,
            fg_color="#991b1b", hover_color="#7f1d1d",
        )
        # Initially hidden; _refresh_action_visibility shows them.
        self.add_files_btn.pack(side="left", padx=(0, 4))
        self.edit_expiry_btn.pack(side="left", padx=4)
        self.edit_limit_btn.pack(side="left", padx=4)
        self.end_share_btn.pack(side="left", padx=4)
        self.add_files_btn.pack_forget()
        self.edit_expiry_btn.pack_forget()
        self.edit_limit_btn.pack_forget()
        self.end_share_btn.pack_forget()

        # Right-aligned button. "Close" gone - Back at the top replaces it.
        ctk.CTkButton(
            btns, text=t("share_detail.save_all_btn"),
            command=self._save_all, width=180,
        ).pack(side="right")

    def _build_public_link_section(self, parent) -> None:
        """Create a hidden bordered section for the public-link URL.
        Revealed by ``_render_public_link`` when the background
        fetch returns non-None data."""
        self._pl_section_label = ctk.CTkLabel(parent, text=t("share_detail.public_link_heading"), anchor="w")
        self._pl_section = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")

        inner = ctk.CTkFrame(self._pl_section, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=8)

        url_row = ctk.CTkFrame(inner, fg_color="transparent")
        url_row.pack(fill="x")
        self._pl_url_var = ctk.StringVar(value="")
        self._pl_url_entry = ctk.CTkEntry(
            url_row, textvariable=self._pl_url_var, state="readonly",
        )
        self._pl_url_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_copy"), width=90, command=self._copy_pl_url,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_open"), width=90, command=self._open_pl_url,
        ).pack(side="left", padx=(4, 0))

        self._pl_info_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            inner, textvariable=self._pl_info_var, anchor="w",
            text_color="gray",
        ).pack(fill="x", pady=(6, 0))

        self._pl_copied_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            inner, textvariable=self._pl_copied_var, anchor="w",
            text_color=("#166534", "#bbf7d0"),
        ).pack(fill="x", pady=(2, 0))

        # Section starts hidden; we don't pack the label/frame yet.

    def _render_public_link(self, pl: Optional[dict]) -> None:
        if not alive(self):
            return  # view torn down while the PL fetch was in flight (C6)
        if not pl:
            return
        url = pl.get("url")
        if not url:
            # Legacy row with the token only stored as hash - no
            # plaintext to show. Skip rather than misleading the user
            # with an empty box.
            return
        self._pl_url_var.set(url)
        bits: list[str] = []
        if pl.get("has_password"):
            bits.append(t("share_detail.pl_password_protected"))
        dl_limit = pl.get("download_limit")
        if dl_limit is not None:
            remaining = pl.get("downloads_remaining")
            bits.append(t("share_detail.pl_downloads",
                          remaining=remaining, limit=dl_limit))
        if pl.get("notify_on_download"):
            bits.append(t("share_detail.pl_notifies"))
        locked = pl.get("locked_until")
        if locked:
            bits.append(t("share_detail.pl_locked"))
        revoked = pl.get("revoked_at")
        if revoked:
            bits.append(t("share_detail.pl_revoked"))
        self._pl_info_var.set("  ·  ".join(bits))
        # Reveal the section now that we have content.
        self._pl_section_label.pack(fill="x", pady=(8, 0))
        self._pl_section.pack(fill="x", pady=(0, 8))

    def _copy_pl_url(self) -> None:
        # Copy + flash "✓ Copied"; warns on the rare clipboard-lock failure.
        copy_to_clipboard_with_feedback(
            self, self._pl_url_var.get(),
            feedback_var=self._pl_copied_var,
            on_fail=lambda: self._toast(t("share_detail.copy_failed_body"), kind="error"),
        )

    def _open_pl_url(self) -> None:
        url = self._pl_url_var.get()
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _load(self) -> None:
        def _fetch():
            return api_pkg.get_share(self._api, self._share_id)

        def _done(share):
            self._share = share
            self._render_after_load()  # guarded internally (C6)

        def _failed(exc):
            if not alive(self):
                return  # view gone; nothing to warn about (C6)
            msg = getattr(exc, "message", None) or str(exc)
            self._toast(f'{t("share_detail.could_not_load_title")}: {msg}', kind="error")
            self._on_back()

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)
        self._load_public_link()

    def _load_public_link(self) -> None:
        """Fetch public-link metadata in the background. The endpoint
        is owner+admin only and returns None when there's no link, so
        recipients / unprivileged users see no section."""
        def _fetch():
            return api_pkg.get_public_link(self._api, self._share_id)

        def _done(pl):
            self._render_public_link(pl)

        def _failed(_exc):
            # Already permissive in the helper; nothing to do here.
            pass

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    @staticmethod
    def _build_meta_bits(s: ShareResponse) -> list[str]:
        """v0.7.1: collapse the three inline copies of this loop into
        one helper so adding/removing a meta field (e.g. download_limit)
        doesn't require touching `_render_after_load`, `_end_share._done`,
        and `_edit_expiry._done` in lockstep."""
        bits = [
            t("share_detail.meta_created",
              date=format_datetime(s.created_at)),
            t("share_detail.meta_expires", when=format_expiry(s.expires_at)),
        ]
        if s.download_limit is not None:
            remaining = s.downloads_remaining
            if remaining is None:
                bits.append(t("share_detail.meta_limit", limit=s.download_limit))
            else:
                bits.append(t("share_detail.meta_downloads",
                              remaining=remaining, limit=s.download_limit))
        if s.message:
            bits.append(s.message)
        return bits

    def _render_after_load(self) -> None:
        if not alive(self):
            return  # view torn down while the load was in flight (C6)
        s = self._share
        if s is None:
            return
        self.title_var.set(s.effective_subject or "(no subject)")
        self.meta_var.set(" · ".join(self._build_meta_bits(s)))
        self.state_pill.setState(s.state)
        self.state_pill.setText(s.state)
        self._render_files(s.files)
        self._refresh_action_visibility()

    def _is_owner(self) -> bool:
        return self._share is not None and self._share.created_by_id == self._me.id

    def _refresh_action_visibility(self) -> None:
        manage = self._can_manage()
        active = self._share is not None and self._share.state == "active"
        # Add-files is OWNER-only (uploads reject non-owners, incl. admins) and
        # leads the row when shown.
        if self._is_owner():
            self.add_files_btn.pack(side="left", padx=(0, 4))
            self.add_files_btn.configure(state="normal" if active else "disabled")
        else:
            self.add_files_btn.pack_forget()
        for btn in (self.edit_expiry_btn, self.edit_limit_btn, self.end_share_btn):
            if manage:
                btn.pack(side="left", padx=4)
            else:
                btn.pack_forget()
            btn.configure(state="normal" if (manage and active) else "disabled")

    def _add_files(self) -> None:
        s = self._share
        if not s or not self._is_owner() or s.state != "active":
            return
        AddFilesDialog(
            self._app_root,
            self._api,
            s.id,
            s.effective_subject,
            getattr(self._me, "share_notify_recipients_default", True),
            on_added=self._load,
            flash=self._flash,
        )

    # ---- Manager actions ----

    def _end_share(self) -> None:
        """v0.6.1: single destructive action - calls expire-now
        (state → expired, files hard-deleted, uploader quota released).
        Replaces the old separate Revoke and Expire-now buttons."""
        s = self._share
        if not s:
            return
        top = self.winfo_toplevel()
        if not mb.confirm(
            top,
            t("share_detail.end_share_confirm_title"),
            t("share_detail.end_share_confirm_body",
              subject=s.effective_subject or t("share_list.no_subject")),
            ok_text=t("share_detail.end_share_ok"),
        ):
            return

        def _do():
            return api_pkg.expire_share_now(self._api, s.id)

        def _done(updated):
            self._share = updated
            if self._on_mutated is not None:
                self._on_mutated()  # refresh the list even if we navigated away
            if not alive(self):
                return  # detail view gone; don't touch its widgets (C6)
            self.meta_var.set(" · ".join(self._build_meta_bits(updated)))
            self.state_pill.setState(updated.state)
            self.state_pill.setText(updated.state)
            self._refresh_action_visibility()
            self._toast(t("share_detail.ended_body"), kind="success")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            self._toast(f'{t("share_detail.end_share_failed_title")}: {msg}', kind="error")

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    def _edit_expiry(self) -> None:
        s = self._share
        if not s:
            return
        top = self.winfo_toplevel()
        dlg = ExpiryDialog(top, current=s.expires_at)
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
                self._on_mutated()  # refresh the list even if we navigated away
            if not alive(self):
                return  # detail view gone; don't touch its widgets (C6)
            self.meta_var.set(" · ".join(self._build_meta_bits(updated)))
            self._toast(t("share_detail.expiry_updated_body"), kind="success")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            self._toast(f'{t("share_detail.edit_failed_title")}: {msg}', kind="error")

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    def _edit_limit(self) -> None:
        """v0.7.1: edit the per-share download-budget cap."""
        s = self._share
        if not s:
            return
        top = self.winfo_toplevel()
        dlg = LimitDialog(top, current=s.download_limit)
        choice = dlg.show_modal()
        if choice is None:
            return
        mode, value = choice

        def _do():
            if mode == "clear":
                return api_pkg.patch_share_download_limit(
                    self._api, s.id, clear=True,
                )
            return api_pkg.patch_share_download_limit(
                self._api, s.id, limit=value,
            )

        def _done(updated):
            self._share = updated
            if self._on_mutated is not None:
                self._on_mutated()  # refresh the list even if we navigated away
            if not alive(self):
                return  # detail view gone; don't touch its widgets (C6)
            self.meta_var.set(" · ".join(self._build_meta_bits(updated)))
            self._toast(t("share_detail.limit_updated_body"), kind="success")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            self._toast(f'{t("share_detail.edit_failed_title")}: {msg}', kind="error")

        run_in_background(self._app_root, _do, on_done=_done, on_failed=_failed)

    # ---- Files ----

    def _render_files(self, files: list[FileInShareResponse]) -> None:
        for child in self.file_scroll.winfo_children():
            child.destroy()
        self._file_rows = {}
        self.file_scroll.grid_columnconfigure(0, weight=1)
        for r, f in enumerate(files):
            row = ctk.CTkFrame(self.file_scroll, fg_color="transparent")
            row.grid(row=r, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=f.original_filename, anchor="w").grid(
                row=0, column=0, sticky="ew", padx=(0, 8)
            )
            ctk.CTkLabel(row, text=human_size(f.size_bytes), anchor="e").grid(
                row=0, column=1, padx=8
            )
            PillLabel(row, text=f.state, state=f.state).grid(row=0, column=2, padx=8)
            # The action cell hosts a variable button set: Download → Pause +
            # Cancel (while downloading) → Resume + Discard (paused/interrupted)
            # → Open + Folder (after a successful save).
            action_cell = ctk.CTkFrame(row, fg_color="transparent")
            action_cell.grid(row=0, column=3, padx=(8, 0))

            # Inline per-file progress (hidden until a download starts): a thin
            # bar spanning the row + a "rate · eta" readout.
            bar = ctk.CTkProgressBar(row, height=6)
            bar.set(0)
            info_var = ctk.StringVar(value="")
            info_lbl = ctk.CTkLabel(
                row, textvariable=info_var, anchor="e", text_color="gray",
            )
            bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=(0, 8), pady=(4, 0))
            info_lbl.grid(row=1, column=3, sticky="e", pady=(4, 0))
            bar.grid_remove()
            info_lbl.grid_remove()
            self._file_rows[f.id] = {
                "bar": bar, "info_var": info_var, "info_lbl": info_lbl,
                "action_cell": action_cell,
                "filename": f.original_filename,
                "downloadable": f.state in ("clean", "ready_unscanned"),
                # Set when a download is spawned or a resumable partial is found.
                "dest": None,
                "cancel": None,
                "pause": None,
                "last": (0, 0),
            }
            self._init_row_actions(f.id)

    def _init_row_actions(self, file_id: str) -> None:
        """Pick a row's initial buttons: a resumable partial (from the
        persistent registry, validated against the on-disk checkpoint) shows
        Resume + Discard; otherwise a plain Download."""
        row = self._file_rows.get(file_id)
        if row is None:
            return
        entry = dlreg.get(file_id) if row["downloadable"] else None
        if entry and entry.get("status") in dlreg.RESUMABLE:
            dest = Path(entry.get("dest", ""))
            if dest.name and dlckpt.part_path(dest).exists() and dlckpt.read(dest):
                row["dest"] = dest
                done = int(entry.get("bytes_done", 0))
                total = int(entry.get("total", 0))
                row["last"] = (done, total)
                self._set_actions_resumable(
                    file_id, interrupted=entry.get("status") == dlreg.INTERRUPTED
                )
                return
            # Stale registry entry (partial deleted externally) - clean it up.
            dlreg.remove(file_id)
        self._set_actions_idle(file_id)

    def _download_one(self, file_id: str, filename: str) -> None:
        dest_str = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title=t("share_detail.save_file_as"),
            initialfile=filename,
        )
        if not dest_str:
            return
        self._spawn_download(file_id, Path(dest_str))

    def _save_all(self) -> None:
        if not self._share:
            return
        top = self.winfo_toplevel()
        downloadable = [
            f for f in self._share.files
            if f.state in ("clean", "ready_unscanned")
        ]
        if not downloadable:
            self._toast(t("share_detail.nothing_to_save_body"), kind="info")
            return
        dir_str = filedialog.askdirectory(
            parent=top, title=t("share_detail.save_all_dir_title"),
        )
        if not dir_str:
            return
        base = Path(dir_str)
        used: set[str] = set()
        for f in downloadable:
            # SECURITY (audit H4): f.original_filename is server-controlled.
            # Never join it raw - a '../' / absolute / UNC / reserved-device
            # name would write outside the chosen folder. Reduce to a safe,
            # de-duplicated leaf and assert containment.
            dest = safe_join(base, f.original_filename, used)
            self._spawn_download(f.id, dest)

    def _show_open_actions(self, row: dict, dest: Path) -> None:
        """After a successful save, replace the row's Download/Cancel button
        with Open (launch the file in its default app) + Folder (reveal it in
        the OS file manager). Re-downloading is still possible by leaving and
        re-opening the share (which re-renders fresh Download buttons)."""
        cell = row.get("action_cell")
        if not alive(cell):
            return
        for child in cell.winfo_children():
            child.destroy()
        # Pack Folder first so it sits to the right of Open (side="right" stacks
        # right-to-left): visual order is [Open] [Folder].
        ctk.CTkButton(
            cell, text=t("share_detail.open_folder_btn"), width=72,
            fg_color="transparent", border_width=1,
            hover_color=("gray85", "gray25"),
            command=lambda p=dest: self._reveal_path(p),
        ).pack(side="right")
        ctk.CTkButton(
            cell, text=t("share_detail.open_file_btn"), width=72,
            command=lambda p=dest: self._open_path(p),
        ).pack(side="right", padx=(0, 4))

    def _open_path(self, path: Path) -> None:
        """Open the downloaded file in its default application."""
        import os
        import subprocess
        import sys

        # Launch the just-downloaded local file in the OS default app via a
        # fixed argv (no shell, no user-controlled command) on a path the user
        # chose - the standard safe pattern, hence the scoped noqa for ruff-S.
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])  # noqa: S603, S607
            else:
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
        except Exception as exc:
            self._toast(t("share_detail.open_failed", detail=str(exc)), kind="error")

    def _reveal_path(self, path: Path) -> None:
        """Reveal the downloaded file in the OS file manager (selected on
        Windows/macOS; opens the containing folder on Linux)."""
        import subprocess
        import sys

        # Fixed-argv OS file-manager launch (no shell) on a user-chosen path -
        # see _open_path; scoped noqa for the ruff-S subprocess false-positives.
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])  # noqa: S603, S607
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])  # noqa: S603, S607
        except Exception as exc:
            self._toast(t("share_detail.open_failed", detail=str(exc)), kind="error")

    # ---- Per-row action-button state machine ----
    #
    # action_cell shows one of: [Download] · [Pause][Cancel] (running) ·
    # [Resume][Discard] (paused/interrupted) · [Open][Folder] (saved). Each
    # setter clears the cell and repacks; side="right" stacks right-to-left, so
    # the secondary action is packed first (rightmost).

    def _clear_actions(self, row: dict):
        cell = row.get("action_cell")
        if not alive(cell):
            return None
        for child in cell.winfo_children():
            child.destroy()
        return cell

    def _set_actions_idle(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        cell = self._clear_actions(row)
        if cell is None:
            return
        btn = ctk.CTkButton(
            cell, text=t("share_detail.download_btn"), width=110,
            command=lambda fid=file_id: self._download_one(fid, row["filename"]),
        )
        if not row.get("downloadable"):
            btn.configure(state="disabled")
        btn.pack(side="right")
        if alive(row["bar"]):
            row["bar"].grid_remove()
        if alive(row["info_lbl"]):
            row["info_lbl"].grid_remove()
        row["info_var"].set("")
        row["cancel"] = None
        row["pause"] = None

    def _set_actions_active(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        cell = self._clear_actions(row)
        if cell is None:
            return
        ctk.CTkButton(
            cell, text=t("share_detail.cancel_btn"), width=72,
            fg_color="transparent", border_width=1, hover_color=("gray85", "gray25"),
            command=lambda fid=file_id: self._cancel_download(fid),
        ).pack(side="right")
        pause_btn = ctk.CTkButton(
            cell, text=t("share_detail.pause_btn"), width=72,
            command=lambda fid=file_id: self._pause_download(fid),
        )
        pause_btn.pack(side="right", padx=(0, 4))
        row["pause_btn"] = pause_btn

    def _set_actions_resumable(self, file_id: str, *, interrupted: bool) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        cell = self._clear_actions(row)
        if cell is None:
            return
        ctk.CTkButton(
            cell, text=t("share_detail.discard_btn"), width=72,
            fg_color="transparent", border_width=1, hover_color=("gray85", "gray25"),
            command=lambda fid=file_id: self._discard_download(fid),
        ).pack(side="right")
        ctk.CTkButton(
            cell, text=t("share_detail.resume_btn"), width=84,
            command=lambda fid=file_id: self._resume_download(fid),
        ).pack(side="right", padx=(0, 4))
        done, total = row.get("last", (0, 0))
        pct = int(done * 100 / total) if total else 0
        if alive(row["bar"]):
            row["bar"].grid()
            row["bar"].set(min(1.0, done / total) if total else 0)
        if alive(row["info_lbl"]):
            row["info_lbl"].grid()
        key = "dl_interrupted" if interrupted else "dl_paused"
        row["info_var"].set(t(f"share_detail.{key}", pct=pct))

    def _cancel_download(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        ev = row.get("cancel")
        if ev is not None:
            ev.set()
        cell = row.get("action_cell")
        if alive(cell):
            for child in cell.winfo_children():
                child.configure(state="disabled")
        row["info_var"].set(t("share_detail.cancelling"))

    def _pause_download(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        ev = row.get("pause")
        if ev is not None:
            ev.set()
        pb = row.get("pause_btn")
        if alive(pb):
            pb.configure(state="disabled", text=t("share_detail.pausing"))
        row["info_var"].set(t("share_detail.pausing"))

    def _resume_download(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        dest = row.get("dest")
        if dest is None:
            self._download_one(file_id, row["filename"])
            return
        self._spawn_download(file_id, dest, resume=True)

    def _discard_download(self, file_id: str) -> None:
        row = self._file_rows.get(file_id)
        if row is None:
            return
        if not mb.confirm(
            self.winfo_toplevel(),
            t("share_detail.discard_confirm_title"),
            t("share_detail.discard_confirm_body", name=row["filename"]),
        ):
            return
        dest = row.get("dest")
        if dest is not None:
            dlckpt.discard(dest)
        dlreg.remove(file_id)
        row["dest"] = None
        row["last"] = (0, 0)
        self._set_actions_idle(file_id)
        self._toast(t("share_detail.discarded"), kind="info")

    def _spawn_download(self, file_id: str, dest: Path, *, resume: bool = False) -> None:
        from ..config import load_config
        conns = load_config().download_connections

        row = self._file_rows.get(file_id)
        tracker = RateEstimator()
        cancel = threading.Event()
        pause = threading.Event()

        # Instant feedback: reveal the inline bar + "Starting…/Resuming…" and
        # show the Pause + Cancel buttons before the worker produces progress.
        if row is not None:
            row["dest"] = dest
            row["cancel"] = cancel
            row["pause"] = pause
            done0, total0 = row.get("last", (0, 0)) if resume else (0, 0)
            row["bar"].set(min(1.0, done0 / total0) if (resume and total0) else 0)
            row["bar"].grid()
            row["info_lbl"].grid()
            row["info_var"].set(
                t("share_detail.dl_resuming") if resume else t("share_detail.dl_starting")
            )
            self._set_actions_active(file_id)

        dlreg.upsert(
            file_id,
            dest=str(dest),
            filename=row["filename"] if row else dest.name,
            total=(row.get("last", (0, 0))[1] if row else 0),
            bytes_done=(row.get("last", (0, 0))[0] if (resume and row) else 0),
            status=dlreg.ACTIVE,
            share_id=getattr(self, "_share_id", None),
        )

        def _do(tick):
            api_pkg.download_file_resumable(
                self._api, file_id, dest=dest, connections=conns,
                on_progress=tick, cancel=cancel, pause=pause,
            )
            return str(dest)

        def _on_progress(done, total):
            if not alive(self) or row is None or not alive(row["bar"]):
                return  # view/row torn down mid-download (C6)
            row["last"] = (done, total)
            # The registry row was created before the size was known; without
            # this a Resume after an app restart has total=0 (client-5).
            dlreg.set_total(file_id, total)
            if cancel.is_set() or pause.is_set():
                return  # don't clobber the "Cancelling…/Pausing…" label
            if total > 0:
                row["bar"].set(min(1.0, done / total))
            rate, eta = tracker.update(done, total)
            row["info_var"].set(f"{format_rate(rate)} · {format_eta(eta)}")

        def _done(path):
            if not alive(self):
                return
            dlreg.remove(file_id)
            if row is not None and alive(row.get("action_cell")):
                if alive(row["bar"]):
                    row["bar"].grid_remove()
                if alive(row["info_lbl"]):
                    row["info_lbl"].grid_remove()
                row["info_var"].set("")
                row["cancel"] = None
                row["pause"] = None
                row["dest"] = None
                # Saved - the buttons become Open (the file) + Folder (reveal it).
                self._show_open_actions(row, Path(path))
            self._toast(t("share_detail.downloaded_body", path=path), kind="success")

        def _failed(exc):
            if not alive(self):
                return
            done, total = row.get("last", (0, 0)) if row else (0, 0)
            if isinstance(exc, DownloadPaused):
                dlreg.set_status(file_id, dlreg.PAUSED, bytes_done=done)
                self._set_actions_resumable(file_id, interrupted=False)
                return
            if isinstance(exc, DownloadCancelled):
                dlreg.remove(file_id)
                self._set_actions_idle(file_id)
                self._toast(t("share_detail.dl_cancelled"), kind="info")
                return
            # Transport/server error → interrupted. Keep the partial for a
            # later Resume if a checkpoint survived; otherwise reset to Download.
            dest_now = row.get("dest") if row else None
            resumable = (
                dest_now is not None
                and dlckpt.part_path(dest_now).exists()
                and dlckpt.read(dest_now) is not None
            )
            if resumable:
                dlreg.set_status(file_id, dlreg.INTERRUPTED, bytes_done=done)
                self._set_actions_resumable(file_id, interrupted=True)
            else:
                dlreg.remove(file_id)
                self._set_actions_idle(file_id)
            msg = getattr(exc, "message", None) or str(exc)
            self._toast(
                f'{t("share_detail.download_failed_title")}: {msg}', kind="error"
            )

        run_with_progress(
            self._app_root, _do,
            on_progress=_on_progress,
            on_done=_done,
            on_failed=_failed,
        )
