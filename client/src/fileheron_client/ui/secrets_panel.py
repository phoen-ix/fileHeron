"""The Secrets tab (server v2.24.0): received, sent and requests.

Built on the ``ShareListPanel`` pattern: the list lives in ``_list_frame``, and
opening a secret, a request or a compose form ``pack_forget``s the list and
packs one view in its place. The panel is also the views' navigator
(``open_secret`` / ``open_request`` / ``new_secret`` / ``new_request`` /
``back_to_list``), so a view can hand over to another - the request page opens
its answer, a sent form opens the new secret's status - without knowing who
hosts it.

Metadata only: no list row and no navigation ever carries a secret's text.
"""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from ..api import ApiClient
from ..formatters import format_datetime, format_expiry
from ..i18n import t
from ..models import MeResponse
from ..secret_format import (
    REQUEST_STATE_FILTERS,
    SECRET_STATE_FILTERS,
    audience_text,
    from_text,
    label_or_placeholder,
    request_party_text,
    request_state,
    secret_state,
    views_cell,
)
from ._async import run_in_background
from .widgets import PillLabel, alive

RECEIVED = "received"
SENT = "sent"
REQUESTS = "requests"

# Per mode: the column header keys and their grid weights.
_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] = {
    RECEIVED: (
        ("secrets.col.label", "secrets.col.from", "secrets.col.views",
         "secrets.col.expires", "secrets.col.state"),
        (4, 4, 1, 2, 2),
    ),
    SENT: (
        ("secrets.col.label", "secrets.col.to", "secrets.col.views",
         "secrets.col.expires", "secrets.col.state"),
        (4, 4, 1, 2, 2),
    ),
    "requests:mine": (
        ("secrets.col.asked_for", "secrets.col.asked", "secrets.col.open_until",
         "secrets.col.state"),
        (5, 4, 2, 2),
    ),
    "requests:asked": (
        ("secrets.col.asked_for", "secrets.col.from", "secrets.col.open_until",
         "secrets.col.state"),
        (5, 4, 2, 2),
    ),
}


class SecretsPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        flash: Optional[Callable[..., None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._me = me
        self._flash = flash
        self._mode = RECEIVED
        self._request_box = "mine"
        self._rows: list = []
        self._open_view: Optional[ctk.CTkFrame] = None
        # Out-of-order guard (the ShareListPanel idiom): only the newest
        # refresh() may paint.
        self._load_seq = 0
        self._build()

    # ---- list ----------------------------------------------------------------

    def _build(self) -> None:
        self._list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._list_frame.pack(fill="both", expand=True)

        top = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))
        self._mode_by_label = {
            t("secrets.box.received"): RECEIVED,
            t("secrets.box.sent"): SENT,
            t("secrets.box.requests"): REQUESTS,
        }
        self._mode_switch = ctk.CTkSegmentedButton(
            top, values=list(self._mode_by_label), command=self._on_mode,
        )
        self._mode_switch.set(t("secrets.box.received"))
        self._mode_switch.pack(side="left")

        self._box_by_label = {
            t("secrets.request_box.mine"): "mine",
            t("secrets.request_box.asked"): "asked",
        }
        self._box_switch = ctk.CTkSegmentedButton(
            top, values=list(self._box_by_label), command=self._on_request_box,
        )
        self._box_switch.set(t("secrets.request_box.mine"))
        # packed only in Requests mode

        if self._me.can_send_secrets:
            ctk.CTkButton(
                top, text=t("secrets.new"), width=130, command=self.new_secret,
            ).pack(side="right")
            ctk.CTkButton(
                top, text=t("secrets.request.new"), width=150, command=self.new_request,
                fg_color="transparent", border_width=1, hover_color=("gray85", "gray25"),
                text_color=("gray10", "gray90"),
            ).pack(side="right", padx=(0, 8))

        row = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 4))
        self.search_var = ctk.StringVar()
        self._search = ctk.CTkEntry(
            row, textvariable=self.search_var,
            placeholder_text=t("secrets.search_placeholder"),
        )
        self._search.pack(side="left", fill="x", expand=True)
        self._search.bind("<Return>", lambda _e: self.refresh())
        self.filter_var = ctk.StringVar()
        self._filter_menu = ctk.CTkOptionMenu(
            row, variable=self.filter_var, values=["-"], width=130,
            command=lambda _v: self.refresh(),
        )
        self._filter_menu.pack(side="left", padx=8)
        ctk.CTkButton(row, text=t("common.refresh"), command=self.refresh, width=90).pack(side="left")

        self._header = ctk.CTkFrame(self._list_frame, fg_color=("gray80", "gray25"), corner_radius=4)
        self._header.pack(fill="x", padx=8, pady=(0, 2))
        self._scroll = ctk.CTkScrollableFrame(self._list_frame, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(self._list_frame, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 8),
        )
        self._apply_mode()

    def _column_key(self) -> str:
        return f"requests:{self._request_box}" if self._mode == REQUESTS else self._mode

    def _apply_mode(self) -> None:
        """Rebuild the filter choices and the header for the current mode."""
        if self._mode == REQUESTS:
            self._box_switch.pack(side="left", padx=(12, 0))
            filters = (("secrets.filter.open", "open"), ("secrets.filter.closed", "closed"),
                       ("secrets.filter.all", "all"))
        else:
            self._box_switch.pack_forget()
            filters = (("secrets.filter.active", "active"), ("secrets.filter.ended", "ended"),
                       ("secrets.filter.all", "all"))
        self._filter_by_label = {t(k): v for k, v in filters}
        labels = list(self._filter_by_label)
        self._filter_menu.configure(values=labels)
        self.filter_var.set(labels[0])

        for child in self._header.winfo_children():
            child.destroy()
        keys, weights = _COLUMNS[self._column_key()]
        for col in range(5):
            self._header.grid_columnconfigure(col, weight=0, uniform="")
            self._scroll.grid_columnconfigure(col, weight=0, uniform="")
        for col, (key, weight) in enumerate(zip(keys, weights, strict=True)):
            self._header.grid_columnconfigure(col, weight=weight, uniform="cols")
            self._scroll.grid_columnconfigure(col, weight=weight, uniform="cols")
            ctk.CTkLabel(
                self._header, text=t(key), anchor="w",
                font=ctk.CTkFont(weight="bold", size=11),
            ).grid(row=0, column=col, sticky="ew", padx=6, pady=4)

    def _on_mode(self, label: str) -> None:
        self._mode = self._mode_by_label.get(label, RECEIVED)
        self._apply_mode()
        self.refresh()

    def _on_request_box(self, label: str) -> None:
        self._request_box = self._box_by_label.get(label, "mine")
        self._apply_mode()
        self.refresh()

    def refresh(self) -> None:
        if self._open_view is not None:
            return  # the list is hidden; back_to_list() refreshes
        mode, box = self._mode, self._request_box
        filter_value = self._filter_by_label.get(self.filter_var.get(), "all")
        q = self.search_var.get().strip()
        self.status_var.set(t("common.loading"))
        self._load_seq += 1
        mine = self._load_seq

        def _fetch():
            if mode == REQUESTS:
                return api_pkg.list_secret_requests(
                    self._api, box=box, q=q, states=REQUEST_STATE_FILTERS[filter_value],
                )
            return api_pkg.list_secrets(
                self._api, box=mode, q=q, states=SECRET_STATE_FILTERS[filter_value],
            )

        def _done(resp):
            if mine != self._load_seq or not alive(self):
                return
            self._rows = list(resp.items)
            if self._rows:
                self.status_var.set(t("secrets.status_count", shown=len(self._rows), total=resp.total))
            else:
                self.status_var.set(self._empty_text(mode, box))
            if self._open_view is None:
                self._paint_rows(mode, box)

        def _failed(exc):
            if mine != self._load_seq or not alive(self):
                return
            msg = exc.localized() if hasattr(exc, "localized") else str(exc)
            self.status_var.set(t("secrets.status_err", detail=msg))

        run_in_background(self._app_root, _fetch, on_done=_done, on_failed=_failed)

    @staticmethod
    def _empty_text(mode: str, box: str) -> str:
        if mode == REQUESTS:
            return t("secrets.empty.requests_mine") if box == "mine" else t("secrets.empty.requests_asked")
        return t("secrets.empty.received") if mode == RECEIVED else t("secrets.empty.sent")

    @staticmethod
    def _passphrase_flag(has_passphrase: bool) -> str:
        # Text, not an emoji: Tk 8.6 cannot draw characters outside the BMP.
        return f"  · {t('secrets.passphrase_flag')}" if has_passphrase else ""

    def _paint_rows(self, mode: str, box: str) -> None:
        for child in self._scroll.winfo_children():
            child.destroy()
        for r, item in enumerate(self._rows):
            if mode == REQUESTS:
                cells = [
                    item.label + self._passphrase_flag(item.has_passphrase),
                    request_party_text(item, box=box),
                    format_datetime(item.expires_at),
                ]
                pill_text, tone = request_state(item.state)

                def opener(_e=None, rid=item.id):
                    self.open_request(rid)
            else:
                label = label_or_placeholder(item.label) + self._passphrase_flag(item.has_passphrase)
                if mode == RECEIVED and item.is_answer:
                    label = t("secrets.answer_row", label=label)
                party = from_text(item) if mode == RECEIVED else audience_text(item.recipient_summary)
                cells = [label, party, views_cell(item, box=mode), format_expiry(item.expires_at)]
                pill_text, tone = secret_state(item.state)

                def opener(_e=None, sid=item.id):
                    self.open_secret(sid)
            for col, text in enumerate(cells):
                # The first two columns carry free text (a label, a list of
                # whom it went to): wrap rather than run into the next cell.
                lbl = ctk.CTkLabel(self._scroll, text=text, anchor="w", justify="left", cursor="hand2",
                                   wraplength=260 if col < 2 else 0)
                lbl.grid(row=r, column=col, sticky="ew", padx=6, pady=2)
                lbl.bind("<Button-1>", opener)
            pill = PillLabel(self._scroll, text=pill_text, state=tone, cursor="hand2", width=110)
            pill.grid(row=r, column=len(cells), sticky="w", padx=6, pady=2)
            pill.bind("<Button-1>", opener)

    # ---- navigation (the views call these) -------------------------------------

    def _swap_in(self, build: Callable[[ctk.CTkFrame], ctk.CTkFrame]) -> None:
        if self._open_view is not None:
            self._open_view.destroy()
            self._open_view = None
        else:
            self._list_frame.pack_forget()
        view = build(self)
        self._open_view = view
        view.pack(fill="both", expand=True)

    def open_secret(self, secret_id: str, *, back: Optional[Callable[[], None]] = None,
                    back_text: Optional[str] = None) -> None:
        from .secret_detail_view import SecretDetailView

        self._swap_in(lambda host: SecretDetailView(
            host, self._app_root, self._api, self._me, secret_id,
            nav=self, on_back=back or self.back_to_list, back_text=back_text,
        ))

    def open_request(self, request_id: str) -> None:
        from .secret_request_detail_view import SecretRequestDetailView

        self._swap_in(lambda host: SecretRequestDetailView(
            host, self._app_root, self._api, self._me, request_id, nav=self,
        ))

    def new_secret(self) -> None:
        from .secret_compose_view import SecretComposeView

        self._swap_in(lambda host: SecretComposeView(
            host, self._app_root, self._api, self._me, nav=self,
        ))

    def new_request(self) -> None:
        from .secret_request_compose_view import SecretRequestComposeView

        self._swap_in(lambda host: SecretRequestComposeView(
            host, self._app_root, self._api, self._me, nav=self,
        ))

    def back_to_list(self) -> None:
        if self._open_view is not None:
            self._open_view.destroy()
            self._open_view = None
        self._list_frame.pack(fill="both", expand=True)
        self.refresh()

    def toast(self, text: str, kind: str = "info") -> None:
        if self._flash is not None:
            self._flash(text, kind=kind)
