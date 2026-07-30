"""Edit-expiry modal - date picker + Never checkbox. v0.4.0 CTk port.

The caller invokes ``show_modal()`` which blocks and returns:

- ``("set", datetime)`` - user picked a future datetime
- ``("clear", None)`` - user checked the Never box
- ``None`` - dialog was cancelled (no-op)

Pre-fills the picker from ``current`` if non-None; otherwise defaults
to ``now + 7 days`` (matches the SPA's ExpiryPicker default) and ticks
the Never box (most expiry edits are either "shorten" or "remove
entirely")."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

import customtkinter as ctk
from .date_entry import DateEntry

from ..i18n import get_locale, t
from .app import center_window


class ExpiryDialog:
    def __init__(self, parent, current: Optional[datetime] = None) -> None:
        self._win = ctk.CTkToplevel(parent)
        self._win.title(t("expiry_dialog.title"))
        center_window(self._win, 460, 300)
        self._win.resizable(False, False)
        self._win.transient(parent)

        default = current if current is not None else (datetime.now() + timedelta(days=7))
        self._result: Optional[Tuple[str, Optional[datetime]]] = None

        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        ctk.CTkLabel(
            outer, text=t("expiry_dialog.intro"), anchor="w",
        ).pack(fill="x", pady=(0, 12))

        # DateEntry is date-only; pair with HH/MM CTk entries
        # to recover the datetime granularity Qt's QDateTimeEdit gave us
        # in one widget.
        date_row = ctk.CTkFrame(outer, fg_color="transparent")
        date_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(date_row, text=t("expiry_dialog.date_label"), width=60, anchor="w").pack(side="left")
        # mindate=today disables past days in the picker grid.
        self._date = DateEntry(
            date_row,
            year=default.year,
            month=default.month,
            day=default.day,
            mindate=datetime.now().date(),
        )
        self._date.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(date_row, text=t("expiry_dialog.time_label"), width=40, anchor="w").pack(side="left", padx=(8, 0))
        self._hour_var = ctk.StringVar(value=f"{default.hour:02d}")
        ctk.CTkEntry(date_row, textvariable=self._hour_var, width=50).pack(side="left")
        ctk.CTkLabel(date_row, text=":").pack(side="left", padx=4)
        self._minute_var = ctk.StringVar(value=f"{default.minute:02d}")
        ctk.CTkEntry(date_row, textvariable=self._minute_var, width=50).pack(side="left")

        self._never_var = ctk.BooleanVar(value=(current is None))
        ctk.CTkCheckBox(
            outer,
            text=t("expiry_dialog.never_label"),
            variable=self._never_var,
            command=self._on_never_toggled,
        ).pack(anchor="w", pady=(8, 4))

        ctk.CTkLabel(
            outer, text=t("expiry_dialog.never_help"),
            wraplength=420, justify="left", text_color="gray",
        ).pack(fill="x", pady=(0, 12))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text=t("common.ok"), command=self._on_ok, width=90).pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("common.cancel"), command=self._win.destroy, width=90, fg_color="gray",
        ).pack(side="right", padx=(0, 8))

        self._win.bind("<Return>", lambda _e: self._on_ok())
        self._win.bind("<Escape>", lambda _e: self._win.destroy())

        # Apply initial enabled/disabled state.
        self._on_never_toggled()

    def _on_never_toggled(self) -> None:
        disabled = self._never_var.get()
        # DateEntry.configure forwards state to its entry + button
        try:
            self._date.configure(state="disabled" if disabled else "normal")
        except Exception:
            pass

    def _on_ok(self) -> None:
        if self._never_var.get():
            self._result = ("clear", None)
            self._win.destroy()
            return
        try:
            hh = int(self._hour_var.get())
            mm = int(self._minute_var.get())
            if not (0 <= hh < 24) or not (0 <= mm < 60):
                raise ValueError
        except ValueError:
            # Cheap inline validation - clear the result and bail.
            from ._messagebox import warn
            warn(
                self._win,
                t("expiry_dialog.err_invalid_time_title"),
                t("expiry_dialog.err_invalid_time_body"),
            )
            return
        d = self._date.get_date()
        chosen = datetime(d.year, d.month, d.day, hh, mm)
        if chosen <= datetime.now():
            from ._messagebox import warn
            warn(
                self._win,
                t("expiry_dialog.err_past_title"),
                t("expiry_dialog.err_past_body"),
            )
            return
        self._result = ("set", chosen)
        self._win.destroy()

    def show_modal(self) -> Optional[Tuple[str, Optional[datetime]]]:
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))
        self._win.wait_window()
        return self._result
