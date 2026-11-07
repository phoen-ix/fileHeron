"""The skeleton the secret and secret-request detail views share.

Both views had the same header (a back button over a scrollable body laid out
as fixed slots), the same toast plumbing, the same "show the links" section
and the same four-line error-toast closure after every background call - five
clones of up to 25 lines (audit 2026-11-07). The content of each slot stays in
the view; this owns only the frame around it.
"""
from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ..i18n import t
from ._async import run_in_background
from .secret_widgets import LinkRow, back_button, help_label
from .widgets import alive


class DetailViewBase(ctk.CTkFrame):
    def __init__(self, master, root: ctk.CTk, api, me, *, nav) -> None:
        super().__init__(master, fg_color="transparent")
        self._app_root = root
        self._api = api
        self._me = me
        self._nav = nav

    # ---- toasts ----------------------------------------------------------------

    def _toast(self, text: str, kind: str = "info") -> None:
        self._nav.toast(text, kind=kind)

    def _toast_failure(self, exc: BaseException) -> None:
        """The error toast every background action ends in: the server's
        message in the user's language when the error carries one."""
        if alive(self):
            msg = exc.localized() if hasattr(exc, "localized") else str(exc)
            self._toast(msg, kind="error")

    # ---- skeleton --------------------------------------------------------------

    def _build_skeleton(self, back_text: str, on_back: Callable[[], None]) -> None:
        """A back button over a scrollable body; the view then grids its slots
        into `self._body`."""
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        back_button(header, back_text, on_back).pack(side="left")
        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=16, pady=(8, 12))
        self._body.grid_columnconfigure(0, weight=1)

    def _slot(self, row: int) -> ctk.CTkFrame:
        """A fixed slot in a fixed order; an empty slot is grid_remove'd so it
        takes no space."""
        frame = ctk.CTkFrame(self._body, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        frame.grid_remove()
        return frame

    @staticmethod
    def _clear(box: ctk.CTkFrame) -> None:
        for child in box.winfo_children():
            child.destroy()

    @staticmethod
    def _show(box: ctk.CTkFrame, visible: bool) -> None:
        if visible:
            box.grid()
        else:
            box.grid_remove()

    # ---- the links section -----------------------------------------------------

    def _render_links(self, host: ctk.CTkFrame, fetch: Callable[[], Any]) -> None:
        """Fetch the recorded links in the background and list them in `host`:
        one row per link, a note when there are none, and the reminder that the
        links are recorded either way."""

        def _done(resp):
            if not alive(self) or not alive(host):
                return
            self._clear(host)
            if not resp.items:
                help_label(host, t("secrets.detail.no_links")).pack(fill="x")
            for item in resp.items:
                caption = item.email or t("secrets.detail.the_link")
                if item.url:
                    LinkRow(host, item.url, caption=caption,
                            on_copy_failed=lambda: self._toast(t("secrets.copy_failed"), kind="error"),
                            ).pack(fill="x", pady=(0, 6))
                else:
                    help_label(host, f"{caption}: {t('secrets.detail.link_unavailable')}").pack(fill="x")
            help_label(host, t("secrets.detail.links_recorded")).pack(fill="x", pady=(2, 0))

        run_in_background(self._app_root, fetch, on_done=_done, on_failed=self._toast_failure)
