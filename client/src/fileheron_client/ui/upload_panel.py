"""New-share form (v0.4.0 CTk port): subject + message + recipient
picker + expiry + optional public link + drag-drop file list +
byte-driven aggregate progress."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk
from tkcalendar import DateEntry

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..models import ShareResponse
from . import _messagebox as mb
from .recipient_picker import RecipientPickerWidget
from .upload_worker import start_upload
from .widgets import human_size

logger = logging.getLogger("fileheron_client.ui.upload")


class UploadPanel(ctk.CTkFrame):
    def __init__(self, master, root: ctk.CTk, api: ApiClient) -> None:
        super().__init__(master, fg_color="transparent")
        self._root = root
        self._api = api
        # Per-upload tracking. Cleared between submits.
        self._files: list[Path] = []
        self._completed = 0
        self._total_bytes = 0
        self._per_file_done: dict[str, int] = {}
        self._build()

    def _build(self) -> None:
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        # Subject
        ctk.CTkLabel(outer, text="Subject", anchor="w").pack(fill="x")
        self.subject_var = ctk.StringVar()
        ctk.CTkEntry(
            outer, textvariable=self.subject_var,
            placeholder_text="(optional — defaults to first filename)",
        ).pack(fill="x", pady=(0, 8))

        # Message
        ctk.CTkLabel(outer, text="Message", anchor="w").pack(fill="x")
        self.message_text = ctk.CTkTextbox(outer, height=80)
        self.message_text.pack(fill="x", pady=(0, 8))

        # Recipients
        ctk.CTkLabel(outer, text="Recipients", anchor="w").pack(fill="x")
        self.recipients = RecipientPickerWidget(outer, self._root, self._api)
        self.recipients.pack(fill="x", pady=(0, 12))

        # Expiry — paired date picker + HH:MM + Never checkbox.
        self._build_expiry_section(outer)

        # Public link
        self._build_public_link_section(outer)

        # File picker (drag-drop dropped in v0.4.10 — see app.py).
        ctk.CTkLabel(
            outer, text="Files", anchor="w"
        ).pack(fill="x")
        self._file_list_frame = ctk.CTkScrollableFrame(outer, fg_color=("gray90", "gray20"), height=160)
        self._file_list_frame.pack(fill="x", pady=(2, 4))
        self._empty_var = ctk.StringVar(value="(no files yet — click Add files…)")
        self._empty_label = ctk.CTkLabel(self._file_list_frame, textvariable=self._empty_var, text_color="gray")
        self._empty_label.pack(pady=20)

        # File-list controls
        files_row = ctk.CTkFrame(outer, fg_color="transparent")
        files_row.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(files_row, text="Add files…", command=self._on_add).pack(side="left")
        ctk.CTkButton(
            files_row, text="Clear list", command=self._on_clear_files,
            fg_color="gray",
        ).pack(side="left", padx=(8, 0))

        # Submit row
        submit_row = ctk.CTkFrame(outer, fg_color="transparent")
        submit_row.pack(fill="x")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(submit_row, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.send_btn = ctk.CTkButton(
            submit_row, text="Create share + upload",
            command=self._on_send, width=180,
        )
        self.send_btn.pack(side="right")

        self.progress = ctk.CTkProgressBar(outer)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.pack_forget()

    def _build_expiry_section(self, parent) -> None:
        ctk.CTkLabel(parent, text="Expires", anchor="w").pack(fill="x")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 4))

        default = datetime.now() + timedelta(days=7)
        self._expiry_date = DateEntry(
            row,
            year=default.year, month=default.month, day=default.day,
            mindate=datetime.now().date(),
            date_pattern="yyyy-mm-dd",
        )
        self._expiry_date.pack(side="left")
        ctk.CTkLabel(row, text="@", width=20).pack(side="left", padx=4)
        self._expiry_hour = ctk.StringVar(value=f"{default.hour:02d}")
        ctk.CTkEntry(row, textvariable=self._expiry_hour, width=50).pack(side="left")
        ctk.CTkLabel(row, text=":", width=10).pack(side="left")
        self._expiry_min = ctk.StringVar(value=f"{default.minute:02d}")
        ctk.CTkEntry(row, textvariable=self._expiry_min, width=50).pack(side="left")

        self._never_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            parent, text="Never expires (revoke manually)",
            variable=self._never_var, command=self._on_never_toggled,
        ).pack(anchor="w", pady=(0, 12))

    def _on_never_toggled(self) -> None:
        state = "disabled" if self._never_var.get() else "normal"
        try:
            self._expiry_date.configure(state=state)
        except Exception:
            pass

    def _build_public_link_section(self, parent) -> None:
        ctk.CTkLabel(parent, text="Public link", anchor="w").pack(fill="x")
        frame = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")
        frame.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=8)

        self._pl_enabled = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            inner, text="Include a public link",
            variable=self._pl_enabled, command=self._on_public_link_toggled,
        ).pack(anchor="w", pady=(0, 6))

        # Sub-form grid: Password / Download-limit / Notify row.
        sub = ctk.CTkFrame(inner, fg_color="transparent")
        sub.pack(fill="x")
        sub.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sub, text="Password", anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._pl_password = ctk.StringVar()
        self._pl_password_entry = ctk.CTkEntry(
            sub, textvariable=self._pl_password, show="*", placeholder_text="(optional)",
        )
        self._pl_password_entry.grid(row=0, column=1, sticky="ew", pady=2)

        ctk.CTkLabel(sub, text="Download limit", anchor="w").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self._pl_limit = ctk.StringVar(value="")
        self._pl_limit_entry = ctk.CTkEntry(
            sub, textvariable=self._pl_limit, placeholder_text="blank = unlimited",
        )
        self._pl_limit_entry.grid(row=1, column=1, sticky="ew", pady=2)

        self._pl_notify = ctk.BooleanVar(value=False)
        self._pl_notify_box = ctk.CTkCheckBox(
            sub, text="Notify me on every download", variable=self._pl_notify,
        )
        self._pl_notify_box.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        for w in (self._pl_password_entry, self._pl_limit_entry, self._pl_notify_box):
            w.configure(state="disabled")

    def _on_public_link_toggled(self) -> None:
        state = "normal" if self._pl_enabled.get() else "disabled"
        for w in (self._pl_password_entry, self._pl_limit_entry, self._pl_notify_box):
            w.configure(state=state)

    # ---- file list helpers ----

    def _on_add(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.winfo_toplevel(), title="Add files")
        for p in paths:
            self._add_file(Path(p))

    def _on_clear_files(self) -> None:
        self._files.clear()
        self._render_file_list()

    def _add_file(self, p: Path) -> None:
        if any(str(p) == str(q) for q in self._files):
            return
        self._files.append(p)
        self._render_file_list()

    def _render_file_list(self) -> None:
        for child in self._file_list_frame.winfo_children():
            child.destroy()
        if not self._files:
            self._empty_label = ctk.CTkLabel(
                self._file_list_frame, textvariable=self._empty_var, text_color="gray",
            )
            self._empty_label.pack(pady=20)
            return
        for p in self._files:
            row = ctk.CTkFrame(self._file_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(row, text=p.name, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                row, text=f"[{human_size(p.stat().st_size)}]", anchor="e", text_color="gray",
            ).pack(side="left", padx=(8, 8))
            ctk.CTkButton(
                row, text="✕", width=28, fg_color="gray",
                command=lambda path=p: self._remove_file(path),
            ).pack(side="right")

    def _remove_file(self, p: Path) -> None:
        self._files = [q for q in self._files if str(q) != str(p)]
        self._render_file_list()

    # ---- expiry + public-link collection ----

    def _collect_expiry(self) -> tuple[Optional[datetime], bool]:
        if self._never_var.get():
            return None, True
        try:
            hh = int(self._expiry_hour.get())
            mm = int(self._expiry_min.get())
            if not (0 <= hh < 24) or not (0 <= mm < 60):
                raise ValueError
        except ValueError:
            mb.warn(
                self.winfo_toplevel(), "Invalid time",
                "Use 00-23 for hour and 00-59 for minute.",
            )
            return None, False
        d = self._expiry_date.get_date()
        chosen = datetime(d.year, d.month, d.day, hh, mm)
        return chosen, False

    def _collect_public_link(self) -> Optional[dict]:
        if not self._pl_enabled.get():
            return None
        try:
            limit_str = self._pl_limit.get().strip()
            limit = int(limit_str) if limit_str else None
        except ValueError:
            mb.warn(
                self.winfo_toplevel(), "Invalid limit",
                "Download limit must be a positive integer, or blank for unlimited.",
            )
            return None
        return {
            "password": self._pl_password.get().strip() or None,
            "download_limit": limit if (limit and limit > 0) else None,
            "notify_on_download": self._pl_notify.get(),
        }

    # ---- submit + upload ----

    def _on_send(self) -> None:
        if not self._files:
            mb.warn(self.winfo_toplevel(), "No files", "Add at least one file.")
            return
        public_link = self._collect_public_link()
        if not self.recipients.has_any() and public_link is None:
            mb.warn(
                self.winfo_toplevel(), "No recipients",
                "Add at least one user or group recipient, or attach an "
                "inline public link.",
            )
            return
        expires_at, never = self._collect_expiry()
        if not never and expires_at is None:
            return  # _collect_expiry already showed an error toast

        self.send_btn.configure(state="disabled", text="Creating…")
        self.status_var.set("Creating share…")

        def _create():
            return api_pkg.create_share(
                self._api,
                kind="outbound",
                recipient_user_ids=self.recipients.user_ids(),
                recipient_group_ids=self.recipients.group_ids(),
                subject=self.subject_var.get().strip() or None,
                message=self.message_text.get("1.0", "end").strip() or None,
                expires_at=expires_at if not never else None,
                expires_at_never=never,
                public_link=public_link,
            )

        def _on_created(share: ShareResponse):
            if public_link is not None:
                pl = getattr(share, "public_link", None)
                url = None
                if isinstance(pl, dict):
                    url = pl.get("url")
                elif pl is not None:
                    url = getattr(pl, "url", None)
                if url:
                    mb.info(
                        self.winfo_toplevel(), "Public link created",
                        f"Save this URL now — it will not be shown again.\n\n{url}",
                    )
            self._start_uploads(share)

        def _on_create_failed(exc):
            self.send_btn.configure(state="normal", text="Create share + upload")
            msg = getattr(exc, "message", None) or str(exc)
            self.status_var.set(f"Error: {msg}")

        from ._async import run_in_background
        run_in_background(self._root, _create, on_done=_on_created, on_failed=_on_create_failed)

    def _start_uploads(self, share: ShareResponse) -> None:
        self.status_var.set(
            f"Share {share.id[:8]} created — uploading {len(self._files)} file(s)…"
        )
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.set(0)
        self._completed = 0
        self._total_bytes = sum(p.stat().st_size for p in self._files)
        self._per_file_done = {}
        for p in self._files:
            start_upload(
                self._root, self._api,
                share_id=share.id,
                file_path=p,
                on_progress=self._on_chunk_progress,
                on_done=self._on_one_done,
                on_failed=self._on_one_failed,
            )

    def _on_chunk_progress(self, path: str, done: int, _total: int) -> None:
        self._per_file_done[path] = done
        if self._total_bytes <= 0:
            return
        done_total = sum(self._per_file_done.values())
        pct = max(0.0, min(1.0, done_total / self._total_bytes))
        self.progress.set(pct)

    def _on_one_done(self, path: str, _file_id: str) -> None:
        try:
            self._per_file_done[path] = Path(path).stat().st_size
        except OSError:
            pass
        self._completed += 1
        if self._completed == len(self._files):
            self.progress.set(1.0)
            self._reset_form_after_send(success=True)
        else:
            self._on_chunk_progress(path, self._per_file_done.get(path, 0), 0)

    def _on_one_failed(self, path: str, message: str) -> None:
        mb.warn(
            self.winfo_toplevel(), "Upload failed",
            f"{Path(path).name}\n\n{message}",
        )
        self._completed += 1
        if self._completed == len(self._files):
            self._reset_form_after_send(success=False)

    def _reset_form_after_send(self, *, success: bool) -> None:
        self.send_btn.configure(state="normal", text="Create share + upload")
        self.progress.pack_forget()
        if success:
            self.status_var.set("Share created and all files uploaded.")
            self.subject_var.set("")
            self.message_text.delete("1.0", "end")
            self.recipients.reset()
            self._files.clear()
            self._render_file_list()
            self._pl_enabled.set(False)
            self._on_public_link_toggled()
            self._pl_password.set("")
            self._pl_limit.set("")
            self._pl_notify.set(False)
        else:
            self.status_var.set("Some uploads failed — see dialogs above.")
