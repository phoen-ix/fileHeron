"""New-share form (v0.4.0 CTk port): subject + message + recipient
picker + expiry + optional public link + drag-drop file list +
byte-driven aggregate progress."""
from __future__ import annotations

import logging
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk
from tkcalendar import DateEntry
from tkinterdnd2 import DND_FILES

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..i18n import get_locale, t
from ..models import ShareResponse
from . import _messagebox as mb
from .recipient_picker import RecipientPickerWidget
from .upload_worker import start_upload
from .widgets import alive, copy_to_clipboard_with_feedback, human_size

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
        ctk.CTkButton(action_row, text=t("upload.add_files_btn"), command=self._on_add).pack(side="left")
        ctk.CTkButton(
            action_row, text=t("upload.clear_list_btn"), command=self._on_clear_files,
            fg_color="gray",
        ).pack(side="left", padx=(8, 0))
        self.send_btn = ctk.CTkButton(
            action_row, text=t("upload.send_btn"),
            command=self._on_send, width=220,
        )
        self.send_btn.pack(side="right")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            action_row, textvariable=self.status_var, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(16, 16))

        # ---- Top form ----

        # Public-link result card — built hidden; revealed at the very top of
        # the form after a share with a public link is created (so the URL is
        # kept + copyable, not dumped in a one-time popup).
        self._build_pl_result_section(outer)

        # Subject + Message (full width)
        self._subject_label = ctk.CTkLabel(outer, text=t("upload.subject_label"), anchor="w")
        self._subject_label.pack(fill="x")
        self.subject_var = ctk.StringVar()
        ctk.CTkEntry(
            outer, textvariable=self.subject_var,
            placeholder_text=t("upload.subject_placeholder"),
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(outer, text=t("upload.message_label"), anchor="w").pack(fill="x")
        self.message_text = ctk.CTkTextbox(outer, height=50)
        self.message_text.pack(fill="x", pady=(0, 8))

        # Two-column row: Recipients (left) | Expires (right)
        two_col = ctk.CTkFrame(outer, fg_color="transparent")
        two_col.pack(fill="x", pady=(0, 8))
        two_col.grid_columnconfigure(0, weight=1, uniform="cols")
        two_col.grid_columnconfigure(1, weight=1, uniform="cols")

        left_col = ctk.CTkFrame(two_col, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(left_col, text=t("upload.recipients_label"), anchor="w").pack(fill="x")
        # v0.5.1: bordered group around the recipients widget so the
        # section reads as one block (matches Expires + Public link).
        rec_box = ctk.CTkFrame(left_col, border_width=1, fg_color="transparent")
        rec_box.pack(fill="x")
        self.recipients = RecipientPickerWidget(rec_box, self._app_root, self._api)
        self.recipients.pack(fill="x", padx=6, pady=6)

        right_col = ctk.CTkFrame(two_col, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._build_expiry_section(right_col)

        # Public link — one compact inline row instead of a boxed
        # 4-row sub-form. Saves ~110 px of vertical space.
        self._build_public_link_section(outer)

        # Files: label + scrollable list. Expands to fill leftover
        # space between the form above and the pinned action row.
        ctk.CTkLabel(outer, text=t("upload.files_label"), anchor="w").pack(fill="x")
        self._file_list_frame = ctk.CTkScrollableFrame(
            outer, fg_color=("gray90", "gray20"), height=80,
        )
        self._file_list_frame.pack(fill="both", expand=True, pady=(2, 0))

        # v0.5.0: drag-drop target + click-to-browse on the file area.
        # The try/except keeps the button-only flow working if
        # tkinterdnd2's Tcl extension fails to load for any reason.
        try:
            self._file_list_frame.drop_target_register(DND_FILES)
            self._file_list_frame.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            logger.warning("tkinterdnd2 drop registration failed; click-only mode")
        self._file_list_frame.bind("<Button-1>", lambda _e: self._on_add())
        self._file_list_frame.configure(cursor="hand2")

        self._empty_var = ctk.StringVar(value=t("upload.files_empty"))
        self._empty_label = ctk.CTkLabel(
            self._file_list_frame, textvariable=self._empty_var, text_color="gray",
            cursor="hand2",
        )
        self._empty_label.bind("<Button-1>", lambda _e: self._on_add())
        self._empty_label.pack(pady=12)

    def _build_expiry_section(self, parent) -> None:
        ctk.CTkLabel(parent, text=t("upload.expires_label"), anchor="w").pack(fill="x")
        # v0.5.1: bordered group around the expiry controls (date,
        # Never checkbox, per-share download limit).
        box = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")
        box.pack(fill="x")
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=6, pady=6)

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(0, 4))

        default = datetime.now() + timedelta(days=7)
        self._expiry_date = DateEntry(
            row,
            year=default.year, month=default.month, day=default.day,
            mindate=datetime.now().date(),
            date_pattern="yyyy-mm-dd",
            # Pin to the app locale (en/de) so the bundle only needs those
            # Babel CLDR files (see pyinstaller.spec babel trim).
            locale=get_locale(),
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
            inner, text=t("upload.never_label"),
            variable=self._never_var, command=self._on_never_toggled,
        ).pack(anchor="w", pady=(4, 4))

        # v0.4.26: per-share download limit for AUTHENTICATED
        # recipients (backend feature shipped in v1.1.0). Separate
        # from the public-link limit further down. Blank = unlimited.
        limit_row = ctk.CTkFrame(inner, fg_color="transparent")
        limit_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(limit_row, text=t("upload.download_limit_label"), anchor="w").pack(side="left")
        self._share_limit = ctk.StringVar(value="")
        ctk.CTkEntry(
            limit_row, textvariable=self._share_limit,
            placeholder_text=t("upload.download_limit_placeholder"), width=80,
        ).pack(side="left", padx=(8, 0))

    def _on_never_toggled(self) -> None:
        state = "disabled" if self._never_var.get() else "normal"
        try:
            self._expiry_date.configure(state=state)
        except Exception:
            pass

    def _build_public_link_section(self, parent) -> None:
        # v0.5.1: bordered group with a collapsible fields-row.
        # Initially only the [✓] Public link checkbox shows inside
        # the box. Ticking the checkbox packs the Password / Limit /
        # Notify row; unticking it hides them again (no more
        # always-greyed-out controls — pack/forget instead of
        # disable/enable). _collect_public_link() returns None when
        # the checkbox is off, so the submit path is unchanged.
        ctk.CTkLabel(parent, text=t("upload.public_link_label"), anchor="w").pack(fill="x")
        box = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")
        box.pack(fill="x", pady=(0, 4))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=6, pady=6)

        self._pl_enabled = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            inner, text=t("upload.include_public_link"),
            variable=self._pl_enabled, command=self._on_public_link_toggled,
        ).pack(anchor="w")

        # Fields row — packed only when _pl_enabled is on.
        self._pl_fields_row = ctk.CTkFrame(inner, fg_color="transparent")
        # NOT packed here — _on_public_link_toggled controls visibility.

        ctk.CTkLabel(self._pl_fields_row, text=t("upload.pl_password"), anchor="w").pack(side="left", padx=(0, 4))
        self._pl_password = ctk.StringVar()
        self._pl_password_entry = ctk.CTkEntry(
            self._pl_fields_row, textvariable=self._pl_password, show="*",
            placeholder_text=t("upload.pl_password_placeholder"), width=140,
        )
        self._pl_password_entry.pack(side="left")

        ctk.CTkLabel(self._pl_fields_row, text=t("upload.pl_limit"), anchor="w").pack(side="left", padx=(12, 4))
        self._pl_limit = ctk.StringVar(value="")
        self._pl_limit_entry = ctk.CTkEntry(
            self._pl_fields_row, textvariable=self._pl_limit,
            placeholder_text=t("upload.pl_limit_placeholder"), width=80,
        )
        self._pl_limit_entry.pack(side="left")

        self._pl_notify = ctk.BooleanVar(value=False)
        self._pl_notify_box = ctk.CTkCheckBox(
            self._pl_fields_row, text=t("upload.pl_notify"), variable=self._pl_notify,
        )
        self._pl_notify_box.pack(side="left", padx=(12, 0))

    def _on_public_link_toggled(self) -> None:
        if self._pl_enabled.get():
            self._pl_fields_row.pack(fill="x", pady=(6, 0))
        else:
            self._pl_fields_row.pack_forget()

    # ---- public-link result card ----

    def _build_pl_result_section(self, parent) -> None:
        self._pl_result_section = ctk.CTkFrame(parent, border_width=1, fg_color="transparent")
        inner = ctk.CTkFrame(self._pl_result_section, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(
            inner, text=t("upload.public_link_result_note"), anchor="w",
            text_color=("#166534", "#bbf7d0"), wraplength=560,
        ).pack(fill="x", pady=(0, 6))
        url_row = ctk.CTkFrame(inner, fg_color="transparent")
        url_row.pack(fill="x")
        self._pl_result_url_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            url_row, textvariable=self._pl_result_url_var, state="readonly",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_copy"), width=80, command=self._copy_result_url,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            url_row, text=t("share_detail.pl_open"), width=80, command=self._open_result_url,
        ).pack(side="left", padx=(4, 0))
        self._pl_result_copied_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            inner, textvariable=self._pl_result_copied_var, anchor="w",
            text_color=("#166534", "#bbf7d0"),
        ).pack(fill="x", pady=(4, 0))
        # Not packed here — revealed by _show_pl_result.

    def _show_pl_result(self, url: str) -> None:
        self._pl_result_url_var.set(url)
        self._pl_result_copied_var.set("")
        # Reveal at the very top of the form (above Subject).
        self._pl_result_section.pack(fill="x", pady=(0, 8), before=self._subject_label)

    def _hide_pl_result(self) -> None:
        try:
            self._pl_result_section.pack_forget()
        except Exception:
            pass

    def _copy_result_url(self) -> None:
        copy_to_clipboard_with_feedback(
            self, self._pl_result_url_var.get(),
            feedback_var=self._pl_result_copied_var,
            on_fail=lambda: mb.warn(
                self.winfo_toplevel(),
                t("share_detail.copy_failed_title"),
                t("share_detail.copy_failed_body"),
            ),
        )

    def _open_result_url(self) -> None:
        url = self._pl_result_url_var.get()
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # ---- file list helpers ----

    def _on_add(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(),
            title=t("upload.add_files_dialog_title"),
        )
        for p in paths:
            self._add_file(Path(p))

    def _on_drop(self, event) -> None:
        # tkinterdnd2 packs paths with spaces in {curly braces}; use
        # the widget's tk.splitlist to parse correctly. Silently skip
        # anything that isn't an existing file (folders, non-files).
        for raw in self._file_list_frame.tk.splitlist(event.data):
            path = Path(raw)
            if path.is_file():
                self._add_file(path)

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
            # Re-create the empty-state label + re-bind its click (the
            # widget is destroyed and recreated on every render so the
            # binding from _build() doesn't carry over).
            self._empty_label = ctk.CTkLabel(
                self._file_list_frame, textvariable=self._empty_var,
                text_color="gray", cursor="hand2",
            )
            self._empty_label.bind("<Button-1>", lambda _e: self._on_add())
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
                self.winfo_toplevel(),
                t("upload.err_invalid_time_title"),
                t("upload.err_invalid_time_body"),
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
                self.winfo_toplevel(),
                t("upload.err_invalid_limit_title"),
                t("upload.err_invalid_limit_body"),
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
                self.winfo_toplevel(),
                t("upload.err_invalid_pl_limit_title"),
                t("upload.err_invalid_pl_limit_body"),
            )
            return None
        return {
            "password": self._pl_password.get().strip() or None,
            "download_limit": limit if (limit and limit > 0) else None,
            "notify_on_download": self._pl_notify.get(),
        }

    # ---- submit + upload ----

    def _on_send(self) -> None:
        # Starting a new share dismisses the previous public-link result card.
        self._hide_pl_result()
        if not self._files:
            mb.warn(
                self.winfo_toplevel(),
                t("upload.err_no_files_title"),
                t("upload.err_no_files_body"),
            )
            return
        public_link = self._collect_public_link()
        if not self.recipients.has_any() and public_link is None:
            mb.warn(
                self.winfo_toplevel(),
                t("upload.err_no_recipients_title"),
                t("upload.err_no_recipients_body"),
            )
            return
        expires_at, never = self._collect_expiry()
        if not never and expires_at is None:
            return  # _collect_expiry already showed an error toast
        share_limit, ok = self._collect_share_limit()
        if not ok:
            return  # _collect_share_limit already showed an error toast

        self.send_btn.configure(state="disabled", text=t("upload.creating"))
        self.status_var.set(t("upload.creating_status"))

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
                    # Persistent inline result (URL + Copy + Open) instead of a
                    # one-time popup — the link is also re-viewable on the
                    # share's detail page.
                    self._show_pl_result(url)
            self._start_uploads(share)

        def _on_create_failed(exc):
            self.send_btn.configure(state="normal", text=t("upload.send_btn"))
            msg = getattr(exc, "message", None) or str(exc)
            self.status_var.set(t("upload.status_err", detail=msg))

        from ._async import run_in_background
        run_in_background(self._app_root, _create, on_done=_on_created, on_failed=_on_create_failed)

    def _start_uploads(self, share: ShareResponse) -> None:
        self.status_var.set(
            t("upload.status_uploading",
              short_id=share.id[:8], n=len(self._files)),
        )
        # v0.5.3: re-pack with side="bottom" matching _build()'s
        # original geometry, otherwise the bar lands at the top of the
        # form (default side="top") instead of in the pinned bottom row.
        self.progress.pack(side="bottom", fill="x", pady=(6, 0))
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
        if not alive(self):
            return  # panel torn down mid-upload (C6)
        self._per_file_done[path] = done
        if self._total_bytes <= 0:
            return
        done_total = sum(self._per_file_done.values())
        pct = max(0.0, min(1.0, done_total / self._total_bytes))
        self.progress.set(pct)

    def _on_one_done(self, path: str, _file_id: str) -> None:
        if not alive(self):
            return  # panel gone; nothing to update (C6)
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
        if not alive(self):
            return  # panel gone; nothing to warn about (C6)
        mb.warn(
            self.winfo_toplevel(),
            t("upload.upload_failed_title"),
            t("upload.upload_failed_body", name=Path(path).name, detail=message),
        )
        self._completed += 1
        if self._completed == len(self._files):
            self._reset_form_after_send(success=False)

    def _reset_form_after_send(self, *, success: bool) -> None:
        self.send_btn.configure(state="normal", text=t("upload.send_btn"))
        self.progress.pack_forget()
        if success:
            self.status_var.set(t("upload.status_success"))
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
            self.status_var.set(t("upload.status_partial"))
