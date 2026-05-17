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
        super().__init__(master)
        self._app_root = root
        self._api = api
        self._files: list[Path] = []
        self._completed = 0
        self._total_bytes = 0
        self._per_file_done: dict[str, int] = {}
        self._build()

    def _build(self) -> None:
        # v0.4.25 layout — designed to fit in 1000x640 without clipping:
        #   row 0: Subject (full width)
        #   row 1: Message textbox (full width, 50px tall)
        #   row 2: 2-col grid — Recipients (left) | Expires (right)
        #   row 3: Public link as ONE compact inline row
        #   row 4: Files header + scrollable list (fills slack)
        #   row 5 (PINNED BOTTOM): Add files / Clear list /
        #          status text / Create share + upload + progress bar
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- Bottom-pinned action area (packed FIRST so side=bottom
        # claims its space; later top-packed widgets fill above it).

        self.progress = ctk.CTkProgressBar(outer)
        self.progress.set(0)
        self.progress.pack(side="bottom", fill="x", pady=(6, 0))
        self.progress.pack_forget()

        action_row = ctk.CTkFrame(outer, fg_color="transparent")
        action_row.pack(side="bottom", fill="x", pady=(6, 0))
        ctk.CTkButton(action_row, text="Add files…", command=self._on_add).pack(side="left")
        ctk.CTkButton(
            action_row, text="Clear list", command=self._on_clear_files,
            fg_color="gray",
        ).pack(side="left", padx=(8, 0))
        self.send_btn = ctk.CTkButton(
            action_row, text="Create share + upload",
            command=self._on_send, width=180,
        )
        self.send_btn.pack(side="right")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            action_row, textvariable=self.status_var, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(16, 16))

        # ---- Top form ----

        # Subject + Message (full width)
        ctk.CTkLabel(outer, text="Subject", anchor="w").pack(fill="x")
        self.subject_var = ctk.StringVar()
        ctk.CTkEntry(
            outer, textvariable=self.subject_var,
            placeholder_text="(optional — defaults to first filename)",
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(outer, text="Message", anchor="w").pack(fill="x")
        self.message_text = ctk.CTkTextbox(outer, height=50)
        self.message_text.pack(fill="x", pady=(0, 8))

        # Two-column row: Recipients (left) | Expires (right)
        two_col = ctk.CTkFrame(outer, fg_color="transparent")
        two_col.pack(fill="x", pady=(0, 8))
        two_col.grid_columnconfigure(0, weight=1, uniform="cols")
        two_col.grid_columnconfigure(1, weight=1, uniform="cols")

        left_col = ctk.CTkFrame(two_col, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(left_col, text="Recipients", anchor="w").pack(fill="x")
        self.recipients = RecipientPickerWidget(left_col, self._app_root, self._api)
        self.recipients.pack(fill="x")

        right_col = ctk.CTkFrame(two_col, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._build_expiry_section(right_col)

        # Public link — one compact inline row instead of a boxed
        # 4-row sub-form. Saves ~110 px of vertical space.
        self._build_public_link_section(outer)

        # Files: label + scrollable list. Expands to fill leftover
        # space between the form above and the pinned action row.
        ctk.CTkLabel(outer, text="Files", anchor="w").pack(fill="x")
        self._file_list_frame = ctk.CTkScrollableFrame(
            outer, fg_color=("gray90", "gray20"), height=80,
        )
        self._file_list_frame.pack(fill="both", expand=True, pady=(2, 0))
        self._empty_var = ctk.StringVar(value="(no files yet — click Add files…)")
        self._empty_label = ctk.CTkLabel(
            self._file_list_frame, textvariable=self._empty_var, text_color="gray",
        )
        self._empty_label.pack(pady=12)

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
        ).pack(anchor="w", pady=(4, 4))

        # v0.4.26: per-share download limit for AUTHENTICATED
        # recipients (backend feature shipped in v1.1.0). Separate
        # from the public-link limit further down. Blank = unlimited.
        limit_row = ctk.CTkFrame(parent, fg_color="transparent")
        limit_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(limit_row, text="Download limit", anchor="w").pack(side="left")
        self._share_limit = ctk.StringVar(value="")
        ctk.CTkEntry(
            limit_row, textvariable=self._share_limit,
            placeholder_text="∞", width=80,
        ).pack(side="left", padx=(8, 0))

    def _on_never_toggled(self) -> None:
        state = "disabled" if self._never_var.get() else "normal"
        try:
            self._expiry_date.configure(state=state)
        except Exception:
            pass

    def _build_public_link_section(self, parent) -> None:
        # v0.4.25: compact one-row public-link controls. Was four
        # stacked rows in a bordered box (~150 px tall); now a single
        # horizontal row (~32 px).
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 4))

        self._pl_enabled = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            row, text="Public link",
            variable=self._pl_enabled, command=self._on_public_link_toggled,
        ).pack(side="left")

        ctk.CTkLabel(row, text="  Password", anchor="w").pack(side="left", padx=(12, 4))
        self._pl_password = ctk.StringVar()
        self._pl_password_entry = ctk.CTkEntry(
            row, textvariable=self._pl_password, show="*",
            placeholder_text="(optional)", width=140,
        )
        self._pl_password_entry.pack(side="left")

        ctk.CTkLabel(row, text="Limit", anchor="w").pack(side="left", padx=(12, 4))
        self._pl_limit = ctk.StringVar(value="")
        self._pl_limit_entry = ctk.CTkEntry(
            row, textvariable=self._pl_limit, placeholder_text="∞", width=80,
        )
        self._pl_limit_entry.pack(side="left")

        self._pl_notify = ctk.BooleanVar(value=False)
        self._pl_notify_box = ctk.CTkCheckBox(
            row, text="Notify on download", variable=self._pl_notify,
        )
        self._pl_notify_box.pack(side="left", padx=(12, 0))

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

    def _collect_share_limit(self) -> tuple[Optional[int], bool]:
        """Return (limit, ok). limit=None means unlimited. ok=False
        means the user entered something that isn't a positive int."""
        raw = self._share_limit.get().strip()
        if not raw:
            return None, True
        try:
            n = int(raw)
            if n <= 0:
                raise ValueError
        except ValueError:
            mb.warn(
                self.winfo_toplevel(), "Invalid download limit",
                "Download limit must be a positive integer, or blank for unlimited.",
            )
            return None, False
        return n, True

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
        share_limit, ok = self._collect_share_limit()
        if not ok:
            return  # _collect_share_limit already showed an error toast

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
                download_limit=share_limit,
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
        run_in_background(self._app_root, _create, on_done=_on_created, on_failed=_on_create_failed)

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
                self._app_root, self._api,
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
