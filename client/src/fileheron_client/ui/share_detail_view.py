"""Share detail — files + per-file download + manager actions.

v0.6.0 refactor: this used to be ``ShareDetailDialog`` — a separate
``CTkToplevel`` window with ``transient`` + ``grab_set``. Users
disliked the extra window. The class is now ``ShareDetailView``, a
``CTkFrame`` that packs into the parent ``ShareListPanel`` in place
of the list. The list panel handles the pack swap; the "← Back"
button at the top calls back into ``on_back`` to return to the list.

Modal sub-dialogs (``mb.info``, ``mb.confirm``, ``ExpiryDialog``) and
the native ``filedialog`` calls stay as overlays — they're small and
fine as pop-ups. Only the detail itself moved in-window."""
from __future__ import annotations

import webbrowser
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


class ShareDetailView(ctk.CTkFrame):
    """In-window share detail (v0.6.0+, was ``ShareDetailDialog``).

    Constructor arguments mirror the old dialog plus ``on_back`` — the
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
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._share_id = share_id
        self._me = me
        self._on_back = on_back
        self._on_mutated = on_mutated
        self._share: Optional[ShareResponse] = None
        self._dl_in_flight = 0

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
            text="← Back",
            width=90,
            height=28,
            fg_color="transparent",
            border_width=1,
            hover_color=("gray85", "gray25"),
            command=self._on_back,
        ).pack(side="left")

        outer = ctk.CTkFrame(self, fg_color="transparent")
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

        # Public-link section (only shown if the share has one;
        # populated by _load_public_link via background fetch).
        self._build_public_link_section(outer)

        ctk.CTkLabel(outer, text="Files", anchor="w").pack(fill="x")
        self.file_scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        self.file_scroll.pack(fill="both", expand=True, pady=(2, 8))

        # Actions row.
        btns = ctk.CTkFrame(outer, fg_color="transparent")
        btns.pack(fill="x")

        self.edit_expiry_btn = ctk.CTkButton(
            btns, text="Edit expiry…", command=self._edit_expiry, width=110,
        )
        # v0.6.1: single destructive "End share" replaces the old
        # Revoke + Expire-now pair. Same backend call as before
        # (POST /api/shares/{id}/expire) — state → expired, files
        # hard-deleted. Red styling because this is now the only
        # destructive manager action.
        self.end_share_btn = ctk.CTkButton(
            btns, text="End share", command=self._end_share, width=110,
            fg_color="#991b1b", hover_color="#7f1d1d",
        )
        # Initially hidden; _refresh_action_visibility shows them.
        self.edit_expiry_btn.pack(side="left", padx=(0, 4))
        self.end_share_btn.pack(side="left", padx=4)
        self.edit_expiry_btn.pack_forget()
        self.end_share_btn.pack_forget()

        # Right-aligned button. "Close" gone — Back at the top replaces it.
        ctk.CTkButton(
            btns, text="Save all to folder…", command=self._save_all, width=160,
        ).pack(side="right")

        # Progress (download).
        self.progress = ctk.CTkProgressBar(outer)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.pack_forget()  # shown when a download is in flight

    def _build_public_link_section(self, parent) -> None:
        """Create a hidden bordered section for the public-link URL.
        Revealed by ``_render_public_link`` when the background
        fetch returns non-None data."""
        self._pl_section_label = ctk.CTkLabel(parent, text="Public link", anchor="w")
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
            url_row, text="Copy", width=70, command=self._copy_pl_url,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            url_row, text="Open", width=70, command=self._open_pl_url,
        ).pack(side="left", padx=(4, 0))

        self._pl_info_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            inner, textvariable=self._pl_info_var, anchor="w",
            text_color="gray",
        ).pack(fill="x", pady=(6, 0))

        # Section starts hidden; we don't pack the label/frame yet.

    def _render_public_link(self, pl: Optional[dict]) -> None:
        if not pl:
            return
        url = pl.get("url")
        if not url:
            # Legacy row with the token only stored as hash — no
            # plaintext to show. Skip rather than misleading the user
            # with an empty box.
            return
        self._pl_url_var.set(url)
        bits: list[str] = []
        if pl.get("has_password"):
            bits.append("Password protected")
        dl_limit = pl.get("download_limit")
        if dl_limit is not None:
            remaining = pl.get("downloads_remaining")
            bits.append(f"Downloads: {remaining}/{dl_limit}")
        if pl.get("notify_on_download"):
            bits.append("Notifies on download")
        locked = pl.get("locked_until")
        if locked:
            bits.append("LOCKED (too many password attempts)")
        revoked = pl.get("revoked_at")
        if revoked:
            bits.append("REVOKED")
        self._pl_info_var.set("  ·  ".join(bits))
        # Reveal the section now that we have content.
        self._pl_section_label.pack(fill="x", pady=(8, 0))
        self._pl_section.pack(fill="x", pady=(0, 8))

    def _copy_pl_url(self) -> None:
        url = self._pl_url_var.get()
        if not url:
            return
        try:
            top = self.winfo_toplevel()
            top.clipboard_clear()
            top.clipboard_append(url)
            top.update()  # ensures the clipboard sticks
        except Exception:
            pass

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
            self._render_after_load()

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self.winfo_toplevel(), "Could not load share", msg)
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
        for btn in (self.edit_expiry_btn, self.end_share_btn):
            if manage:
                btn.pack(side="left", padx=(0, 4)) if btn is self.edit_expiry_btn else btn.pack(side="left", padx=4)
            else:
                btn.pack_forget()
            btn.configure(state="normal" if (manage and active) else "disabled")

    # ---- Manager actions ----

    def _end_share(self) -> None:
        """v0.6.1: single destructive action — calls expire-now
        (state → expired, files hard-deleted, uploader quota released).
        Replaces the old separate Revoke and Expire-now buttons."""
        s = self._share
        if not s:
            return
        top = self.winfo_toplevel()
        if not mb.confirm(
            top,
            "End share",
            (
                f"End this share now and delete all uploaded files? "
                f"This cannot be undone.\n\n"
                f"Subject: {s.effective_subject or '(no subject)'}"
            ),
            ok_text="End share",
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
            mb.info(top, "Ended", "Share ended and files deleted.")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(top, "End share failed", msg)

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
                self._on_mutated()
            bits = [
                f"Created {updated.created_at.strftime('%Y-%m-%d %H:%M')}",
                f"Expires {format_expiry(updated.expires_at)}",
            ]
            if updated.message:
                bits.append(updated.message)
            self.meta_var.set(" · ".join(bits))
            mb.info(top, "Updated", "Share expiry updated.")

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(top, "Edit failed", msg)

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
            parent=self.winfo_toplevel(), title="Save file as", initialfile=filename,
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
            mb.info(top, "Nothing to save", "No downloadable files.")
            return
        dir_str = filedialog.askdirectory(parent=top, title="Save all to folder")
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
            mb.info(self.winfo_toplevel(), "Downloaded", f"Saved to:\n{path}")

        def _failed(exc):
            self._dl_in_flight -= 1
            if self._dl_in_flight <= 0:
                self.progress.pack_forget()
            msg = getattr(exc, "message", None) or str(exc)
            mb.warn(self.winfo_toplevel(), "Download failed", msg)

        run_with_progress(
            self._app_root, _do,
            on_progress=_on_progress,
            on_done=_done,
            on_failed=_failed,
        )
