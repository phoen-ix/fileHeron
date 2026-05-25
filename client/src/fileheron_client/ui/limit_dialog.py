"""Edit-download-limit modal — integer entry + Unlimited checkbox.

v0.7.1. The caller invokes ``show_modal()`` which blocks and returns:

- ``("set", int)`` — user entered a positive integer
- ``("clear", None)`` — user checked the Unlimited box
- ``None`` — dialog was cancelled (no-op)

Twin of ``expiry_dialog.py`` — same modal pattern, same return shape
so ``share_detail_view._edit_limit`` can mirror ``_edit_expiry``."""
from __future__ import annotations

from typing import Optional, Tuple

import customtkinter as ctk

from ..i18n import t


class LimitDialog:
    def __init__(self, parent, current: Optional[int] = None) -> None:
        self._win = ctk.CTkToplevel(parent)
        self._win.title(t("limit_dialog.title"))
        self._win.geometry("420x260")
        self._win.resizable(False, False)
        self._win.transient(parent)

        self._result: Optional[Tuple[str, Optional[int]]] = None

        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        ctk.CTkLabel(
            outer, text=t("limit_dialog.intro"),
            wraplength=380, justify="left",
        ).pack(fill="x", pady=(0, 12))

        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text=t("limit_dialog.new_limit_label"), width=80, anchor="w").pack(side="left")
        # Pre-fill with the current value if set, else placeholder "e.g. 10".
        self._limit_var = ctk.StringVar(
            value="" if current is None else str(current)
        )
        self._limit_entry = ctk.CTkEntry(
            row, textvariable=self._limit_var, width=120,
            placeholder_text=t("limit_dialog.limit_placeholder"),
        )
        self._limit_entry.pack(side="left")

        self._unlimited_var = ctk.BooleanVar(value=(current is None))
        ctk.CTkCheckBox(
            outer,
            text=t("limit_dialog.unlimited_label"),
            variable=self._unlimited_var,
            command=self._on_unlimited_toggled,
        ).pack(anchor="w", pady=(8, 4))

        ctk.CTkLabel(
            outer, text=t("limit_dialog.unlimited_help"),
            wraplength=380, justify="left", text_color="gray",
        ).pack(fill="x", pady=(0, 12))

        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text=t("common.ok"), command=self._on_ok, width=90).pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("common.cancel"), command=self._win.destroy,
            width=90, fg_color="gray",
        ).pack(side="right", padx=(0, 8))

        self._win.bind("<Return>", lambda _e: self._on_ok())
        self._win.bind("<Escape>", lambda _e: self._win.destroy())

        self._on_unlimited_toggled()

    def _on_unlimited_toggled(self) -> None:
        disabled = self._unlimited_var.get()
        try:
            self._limit_entry.configure(
                state="disabled" if disabled else "normal"
            )
        except Exception:
            pass

    def _on_ok(self) -> None:
        if self._unlimited_var.get():
            self._result = ("clear", None)
            self._win.destroy()
            return
        raw = self._limit_var.get().strip()
        try:
            n = int(raw)
            if n <= 0:
                raise ValueError
        except ValueError:
            from ._messagebox import warn
            warn(
                self._win,
                t("limit_dialog.err_invalid_title"),
                t("limit_dialog.err_invalid_body"),
            )
            return
        self._result = ("set", n)
        self._win.destroy()

    def show_modal(self) -> Optional[Tuple[str, Optional[int]]]:
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))
        self._win.wait_window()
        return self._result
