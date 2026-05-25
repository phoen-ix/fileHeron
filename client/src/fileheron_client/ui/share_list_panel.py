"""Inbox / Outbox table panel — v0.4.0 CustomTkinter port.

CTk doesn't have a real "table" widget. We render rows as
``CTkFrame``s inside a ``CTkScrollableFrame``; each row is a
horizontal layout of labels + the PillLabel state chip. More verbose
than QTableWidget but the look matches the rest of the CTk UI.

The list endpoint already pages at 200 by default, so the row count
stays manageable even on busy instances.

v0.6.0: this panel now owns the drill-down into ``ShareDetailView``
instead of delegating up to ``MainWindow``. The list UI lives inside
``self._list_frame``; on row click we ``pack_forget`` that and
``pack`` a fresh ``ShareDetailView`` in its place. The view's
"← Back" button calls back into ``_drill_out`` which swaps the list
back in and refreshes."""
from __future__ import annotations

from typing import Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient, ApiError
from ..formatters import format_expiry
from ..models import MeResponse, ShareListItem
from ._async import run_in_background
from .share_detail_view import ShareDetailView
from .widgets import PillLabel, human_size


# Column header labels + their grid-column widths. Keep COL_WIDTHS in
# sync with how each cell is sized below.
_COLS = ("Subject", "Party", "Files", "Size", "Created", "Expires", "State")
_COL_WEIGHTS = (4, 3, 1, 1, 2, 2, 1)


_STATE_FILTERS: list[tuple[str, str]] = [
    ("Active", "active"),
    ("Any state", ""),
    ("Expired", "expired"),
    ("Revoked", "revoked"),
    ("Deleted", "deleted"),
]


class ShareListPanel(ctk.CTkFrame):
    """Used twice — for the Inbox tab (box=inbox, shows sender) and
    the Outbox tab (box=outbox, shows recipients)."""

    def __init__(
        self,
        master,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        box: str,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._me = me
        self._box = box
        self._items: list[ShareListItem] = []
        self._detail_view: Optional[ShareDetailView] = None
        self._build()
        # Initial load is kicked by main_window after deiconify so the
        # first paint happens after the window is visible.

    def _build(self) -> None:
        # All list-mode widgets live inside _list_frame so drill-in
        # can hide them with one pack_forget call.
        self._list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._list_frame.pack(fill="both", expand=True)

        # Filter row.
        row = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(8, 4))

        self.search_var = ctk.StringVar()
        search = ctk.CTkEntry(
            row, textvariable=self.search_var, placeholder_text="Search subject…"
        )
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", lambda _e: self.refresh())

        self.state_filter_var = ctk.StringVar(value=_STATE_FILTERS[0][0])
        state_menu = ctk.CTkOptionMenu(
            row,
            variable=self.state_filter_var,
            values=[label for label, _ in _STATE_FILTERS],
            command=lambda _v: self.refresh(),
            width=120,
        )
        state_menu.pack(side="left", padx=(8, 8))

        ctk.CTkButton(row, text="Refresh", command=self.refresh, width=90).pack(side="left")

        # Header row (sticky above the scrollable content).
        header = ctk.CTkFrame(self._list_frame, fg_color=("gray80", "gray25"), corner_radius=4)
        header.pack(fill="x", padx=8, pady=(0, 2))
        for col_idx, (col_name, weight) in enumerate(zip(_COLS, _COL_WEIGHTS)):
            header.grid_columnconfigure(col_idx, weight=weight, uniform="cols")
            ctk.CTkLabel(
                header,
                text=col_name,
                anchor="w",
                font=ctk.CTkFont(weight="bold", size=11),
            ).grid(row=0, column=col_idx, sticky="ew", padx=6, pady=4)

        # Scrollable body — rows added by _render.
        self._scroll = ctk.CTkScrollableFrame(self._list_frame, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        for col_idx, weight in enumerate(_COL_WEIGHTS):
            self._scroll.grid_columnconfigure(col_idx, weight=weight, uniform="cols")

        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(self._list_frame, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 8)
        )

    def refresh(self) -> None:
        label = self.state_filter_var.get()
        state_value = next(
            (v for (lbl, v) in _STATE_FILTERS if lbl == label), ""
        )
        states = [state_value] if state_value else None
        self.status_var.set("Loading…")

        def _fetch():
            return api_pkg.list_shares(
                self._api,
                box=self._box,
                q=self.search_var.get().strip(),
                states=states,
                page=1,
                page_size=200,
            )

        def _done(resp):
            self._items = resp.items
            self.status_var.set(f"{len(resp.items)} of {resp.total} shares")
            self._render()

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            self.status_var.set(f"Error: {msg}")

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _render(self) -> None:
        # Clear old rows.
        for child in self._scroll.winfo_children():
            child.destroy()

        for r, item in enumerate(self._items):
            subject = item.effective_subject or "(no subject)"
            if self._box == "inbox":
                party = item.sender.display_name if item.sender else "(unknown)"
            else:
                party = ", ".join(rec.label for rec in item.recipients) or "(none)"

            # Plain text cells — manually grid each into the scrollable
            # frame. The PillLabel state chip is a widget (cell 6).
            cells = [
                subject,
                party,
                str(item.file_count),
                human_size(item.total_size_bytes),
                item.created_at.strftime("%Y-%m-%d %H:%M"),
                format_expiry(item.expires_at),
            ]
            # Single-click AND double-click drill into the share detail.
            # v0.6.0: routing is internal (no more on_open_share kwarg).
            open_handler = lambda _e, sid=item.id: self._drill_in(sid)
            for col_idx, text in enumerate(cells):
                lbl = ctk.CTkLabel(
                    self._scroll, text=text, anchor="w", justify="left",
                    wraplength=0, cursor="hand2",
                )
                lbl.grid(row=r, column=col_idx, sticky="ew", padx=6, pady=2)
                lbl.bind("<Button-1>", open_handler)
                lbl.bind("<Double-Button-1>", open_handler)

            pill = PillLabel(
                self._scroll, text=item.state, state=item.state, cursor="hand2",
            )
            pill.grid(row=r, column=6, sticky="w", padx=6, pady=2)
            pill.bind("<Button-1>", open_handler)
            pill.bind("<Double-Button-1>", open_handler)

    # ---- drill-down navigation (v0.6.0) ---------------------------------

    def _drill_in(self, share_id: str) -> None:
        """Hide the list, pack a fresh ShareDetailView in its place."""
        if self._detail_view is not None:
            # Already viewing something — replace with the new share.
            self._detail_view.destroy()
            self._detail_view = None
        else:
            self._list_frame.pack_forget()
        self._detail_view = ShareDetailView(
            self,
            self._app_root,
            self._api,
            share_id,
            self._me,
            on_back=self._drill_out,
            on_mutated=self.refresh,
        )
        self._detail_view.pack(fill="both", expand=True)

    def _drill_out(self) -> None:
        """Destroy the detail view, restore the list, refresh."""
        if self._detail_view is not None:
            self._detail_view.destroy()
            self._detail_view = None
        self._list_frame.pack(fill="both", expand=True)
        self.refresh()
