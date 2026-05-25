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
from ..i18n import t
from ..models import MeResponse, ShareListItem
from ._async import run_in_background
from .share_detail_view import ShareDetailView
from .widgets import PillLabel, human_size


# Column-header i18n keys + grid weights. Resolved at build time so
# they pick up the active locale; not re-resolved on locale switch.
_COL_KEYS = (
    "share_list.col_subject",
    "share_list.col_party",
    "share_list.col_files",
    "share_list.col_size",
    "share_list.col_created",
    "share_list.col_expires",
    "share_list.col_state",
)
_COL_WEIGHTS = (4, 3, 1, 1, 2, 2, 1)


# (i18n_key, server_value) — same shape as the v0.7.x English tuples.
_STATE_FILTER_KEYS: list[tuple[str, str]] = [
    ("share_list.state_active", "active"),
    ("share_list.state_any", ""),
    ("share_list.state_expired", "expired"),
    ("share_list.state_revoked", "revoked"),
    ("share_list.state_deleted", "deleted"),
]


# v0.7.2: sort options match the SPA's GET /api/shares ?sort= values.
_SORT_OPTION_KEYS: list[tuple[str, str]] = [
    ("share_list.sort_created", "created_at"),
    ("share_list.sort_expires", "expires_at"),
    ("share_list.sort_subject", "subject"),
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

        # ---- Row 1: search + state filter + refresh
        row = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(8, 4))

        self.search_var = ctk.StringVar()
        search = ctk.CTkEntry(
            row, textvariable=self.search_var,
            placeholder_text=t("share_list.search_placeholder"),
        )
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", lambda _e: self.refresh())

        # Cache resolved label→value maps for the two enum-style filters
        # so refresh() doesn't repeatedly walk the constant tuples.
        self._state_label_to_value = {t(k): v for (k, v) in _STATE_FILTER_KEYS}
        self._sort_label_to_value = {t(k): v for (k, v) in _SORT_OPTION_KEYS}

        state_labels = list(self._state_label_to_value.keys())
        self.state_filter_var = ctk.StringVar(value=state_labels[0])
        state_menu = ctk.CTkOptionMenu(
            row,
            variable=self.state_filter_var,
            values=state_labels,
            command=lambda _v: self.refresh(),
            width=120,
        )
        state_menu.pack(side="left", padx=(8, 8))

        ctk.CTkButton(row, text=t("common.refresh"), command=self.refresh, width=90).pack(side="left")

        # ---- Row 2 (v0.7.2): sort + direction + party filter
        row2 = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(row2, text=t("share_list.sort_label"), anchor="w").pack(side="left")
        sort_labels = list(self._sort_label_to_value.keys())
        self.sort_var = ctk.StringVar(value=sort_labels[0])
        sort_menu = ctk.CTkOptionMenu(
            row2,
            variable=self.sort_var,
            values=sort_labels,
            command=lambda _v: self.refresh(),
            width=120,
        )
        sort_menu.pack(side="left", padx=(6, 6))

        # Direction toggle: starts at desc (newest/farthest first — matches
        # the backend default + the old client behaviour).
        self._direction = "desc"
        self._direction_btn = ctk.CTkButton(
            row2, text=t("share_list.direction_desc"),
            command=self._toggle_direction, width=110,
        )
        self._direction_btn.pack(side="left", padx=(0, 12))

        # Party filter — distinct senders (inbox) or recipient users
        # (outbox) seen on the current page. Lightweight: no server
        # search, just the parties already present in _items. Refilled
        # by ``_rebuild_party_options`` after every successful refresh.
        self._any_party_label = t("share_list.party_any")
        party_label = (
            t("share_list.party_inbox") if self._box == "inbox"
            else t("share_list.party_outbox")
        )
        ctk.CTkLabel(row2, text=party_label, anchor="w").pack(side="left")
        self.party_var = ctk.StringVar(value=self._any_party_label)
        self._party_menu = ctk.CTkOptionMenu(
            row2,
            variable=self.party_var,
            values=[self._any_party_label],
            command=lambda _v: self.refresh(),
            width=180,
        )
        self._party_menu.pack(side="left", padx=(6, 0))
        # Map: party label → user_id (None for the "Anyone" sentinel).
        # Refilled by ``_rebuild_party_options`` on each refresh.
        self._party_id_by_label: dict[str, Optional[int]] = {self._any_party_label: None}

        # Header row (sticky above the scrollable content).
        header = ctk.CTkFrame(self._list_frame, fg_color=("gray80", "gray25"), corner_radius=4)
        header.pack(fill="x", padx=8, pady=(0, 2))
        col_labels = [t(k) for k in _COL_KEYS]
        for col_idx, (col_name, weight) in enumerate(zip(col_labels, _COL_WEIGHTS)):
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

    def _toggle_direction(self) -> None:
        self._direction = "asc" if self._direction == "desc" else "desc"
        self._direction_btn.configure(
            text=t("share_list.direction_asc") if self._direction == "asc"
            else t("share_list.direction_desc"),
        )
        self.refresh()

    def _rebuild_party_options(self) -> None:
        """v0.7.2: derive the Sender/Recipient OptionMenu values from
        the parties present in the current page of items. Cheap, no
        server search needed, and gives an immediate "filter to Alice"
        UX. Loss: parties not visible on the current page aren't
        selectable — acceptable given the 200-row page cap."""
        seen: dict[int, str] = {}
        if self._box == "inbox":
            for it in self._items:
                if it.sender is not None:
                    seen.setdefault(it.sender.id, it.sender.display_name)
        else:
            for it in self._items:
                for rec in it.recipients:
                    if rec.kind == "user":
                        seen.setdefault(rec.id, rec.label)
        labels = [self._any_party_label] + sorted(seen.values(), key=str.casefold)
        self._party_id_by_label = {self._any_party_label: None}
        for uid, label in seen.items():
            self._party_id_by_label[label] = uid
        # Preserve the current selection if still present; else reset.
        current = self.party_var.get()
        if current not in labels:
            self.party_var.set(self._any_party_label)
        self._party_menu.configure(values=labels)

    def refresh(self) -> None:
        state_value = self._state_label_to_value.get(
            self.state_filter_var.get(), "",
        )
        states = [state_value] if state_value else None
        sort_value = self._sort_label_to_value.get(
            self.sort_var.get(), "created_at",
        )
        party_id = self._party_id_by_label.get(self.party_var.get(), None)
        # v0.7.2: split the same picker between the two backend params
        # depending on which box we're showing.
        sender_user_id: Optional[int] = party_id if self._box == "inbox" else None
        recipient_user_id: Optional[int] = party_id if self._box == "outbox" else None
        self.status_var.set(t("common.loading"))

        def _fetch():
            return api_pkg.list_shares(
                self._api,
                box=self._box,
                q=self.search_var.get().strip(),
                states=states,
                page=1,
                page_size=200,
                sort=sort_value,
                direction=self._direction,
                sender_user_id=sender_user_id,
                recipient_user_id=recipient_user_id,
            )

        def _done(resp):
            self._items = resp.items
            self.status_var.set(
                t("share_list.status_count",
                  shown=len(resp.items), total=resp.total),
            )
            self._rebuild_party_options()
            # v0.6.2: skip re-grid while drilled in. The list frame is
            # pack_forgot during drill-in, so a render() during that
            # time is just wasted CPU + GC churn (and a latent footgun
            # if a future refactor assumes _render only runs while the
            # list is visible). _drill_out fires its own refresh().
            if self._detail_view is None:
                self._render()

        def _failed(exc):
            msg = getattr(exc, "message", None) or str(exc)
            self.status_var.set(t("share_list.status_err", detail=msg))

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    def _render(self) -> None:
        # Clear old rows.
        for child in self._scroll.winfo_children():
            child.destroy()

        for r, item in enumerate(self._items):
            subject = item.effective_subject or t("share_list.no_subject")
            if self._box == "inbox":
                party = (
                    item.sender.display_name if item.sender
                    else t("share_list.unknown")
                )
            else:
                party = (
                    ", ".join(rec.label for rec in item.recipients)
                    or t("share_list.no_party")
                )

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
