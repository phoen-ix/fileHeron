"""Date entry + month-grid popup, built on stdlib tkinter and CustomTkinter.

Replaces tkcalendar.DateEntry. tkcalendar is GPL-3.0, and it was being compiled
into the MIT-licensed .exe published for public download - a licence conflict in
a shipped artifact, and a violation of this project's only-permissive-deps rule
(audit 2026-07-30). Dropping it also drops its Babel dependency, which is why
pyinstaller.spec no longer has to hand-trim Babel's CLDR locale data out of the
bundle.

Deliberately narrow: this implements only what the two call sites use, in the
one format the app uses (ISO yyyy-mm-dd, which is also what the backend expects).
No locale-dependent parsing, which is what made the tkcalendar integration need a
pinned `locale=` argument and a matching CLDR trim in the first place.

CustomTkinter traps observed here:
- The popup is a plain `tk.Toplevel` with `overrideredirect(True)`, NOT a
  CTkToplevel. CTkToplevel's titlebar-colour routine withdraws and re-shows the
  window, and that deiconify can get lost during construction, leaving an
  invisible window.
- No attribute here shadows a `tkinter.Misc` member (notably `_root`), which
  breaks Tk event dispatch.
"""
from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import customtkinter as ctk

_ISO = "%Y-%m-%d"


class DateEntry(ctk.CTkFrame):
    """An entry showing an ISO date, plus a button opening a month grid.

    Only the surface the app needs: `get_date()`, `set_date()`, and
    `configure(state=...)`.
    """

    def __init__(
        self,
        master,
        *,
        year: Optional[int] = None,
        month: Optional[int] = None,
        day: Optional[int] = None,
        mindate: Optional[date] = None,
        width: int = 130,
        on_change: Optional[Callable[[date], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        today = date.today()
        initial = date(year or today.year, month or today.month, day or today.day)
        self._mindate = mindate
        self._on_change = on_change
        self._popup: Optional[tk.Toplevel] = None
        # The month the grid is showing; not necessarily the selected date.
        self._view = initial.replace(day=1)

        self._var = ctk.StringVar(value=initial.strftime(_ISO))
        self._entry = ctk.CTkEntry(self, textvariable=self._var, width=width)
        self._entry.pack(side="left")
        self._button = ctk.CTkButton(self, text="▾", width=28, command=self._toggle_popup)
        self._button.pack(side="left", padx=(4, 0))

    # -- public API ---------------------------------------------------------

    def get_date(self) -> date:
        """Parse the entry. Raises ValueError on anything unparseable, which is
        what the callers already handle for the HH/MM fields beside it."""
        return datetime.strptime(self._var.get().strip(), _ISO).date()

    def set_date(self, value: date) -> None:
        self._var.set(value.strftime(_ISO))
        self._view = value.replace(day=1)

    def configure(self, **kwargs):  # noqa: ANN201 - matches CTk's signature
        """Intercept `state`, which CTkFrame does not accept, and apply it to
        the child widgets instead."""
        state = kwargs.pop("state", None)
        if state is not None:
            self._entry.configure(state=state)
            self._button.configure(state=state)
            if state == "disabled":
                self._close_popup()
        if kwargs:
            super().configure(**kwargs)
        return None

    # -- popup --------------------------------------------------------------

    def _toggle_popup(self) -> None:
        if self._popup is not None:
            self._close_popup()
            return
        try:
            self._view = self.get_date().replace(day=1)
        except ValueError:
            self._view = date.today().replace(day=1)
        self._open_popup()

    def _open_popup(self) -> None:
        # Plain Toplevel + overrideredirect: no titlebar, so CTkToplevel's
        # titlebar handling (and its withdraw/deiconify trap) never runs.
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        self._popup = popup

        frame = ctk.CTkFrame(popup)
        frame.pack(fill="both", expand=True)
        self._grid_holder = ctk.CTkFrame(frame, fg_color="transparent")

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(6, 0))
        ctk.CTkButton(header, text="‹", width=28, command=self._prev_month).pack(side="left")
        self._header_label = ctk.CTkLabel(header, text="", width=140)
        self._header_label.pack(side="left", expand=True)
        ctk.CTkButton(header, text="›", width=28, command=self._next_month).pack(side="left")

        self._grid_holder.pack(fill="both", expand=True, padx=6, pady=6)
        self._render_month()

        popup.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        popup.geometry(f"+{x}+{y}")
        popup.bind("<Escape>", lambda _e: self._close_popup())
        popup.focus_set()

    def _close_popup(self) -> None:
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass  # already gone (parent destroyed first)
            self._popup = None

    def _prev_month(self) -> None:
        first = self._view
        self._view = (first.replace(day=1) - timedelta(days=1)).replace(day=1)
        self._render_month()

    def _next_month(self) -> None:
        days = calendar.monthrange(self._view.year, self._view.month)[1]
        self._view = (self._view.replace(day=days) + timedelta(days=1)).replace(day=1)
        self._render_month()

    def _render_month(self) -> None:
        for child in self._grid_holder.winfo_children():
            child.destroy()
        self._header_label.configure(text=self._view.strftime("%B %Y"))

        for col, name in enumerate(calendar.weekheader(2).split()):
            ctk.CTkLabel(self._grid_holder, text=name, width=30).grid(row=0, column=col, padx=1, pady=1)

        for r, week in enumerate(calendar.Calendar().monthdatescalendar(self._view.year, self._view.month), start=1):
            for c, day in enumerate(week):
                if day.month != self._view.month:
                    ctk.CTkLabel(self._grid_holder, text="", width=30).grid(row=r, column=c, padx=1, pady=1)
                    continue
                disabled = self._mindate is not None and day < self._mindate
                ctk.CTkButton(
                    self._grid_holder,
                    text=str(day.day),
                    width=30,
                    state="disabled" if disabled else "normal",
                    command=(lambda d=day: self._pick(d)),
                ).grid(row=r, column=c, padx=1, pady=1)

    def _pick(self, value: date) -> None:
        self.set_date(value)
        self._close_popup()
        if self._on_change is not None:
            self._on_change(value)
