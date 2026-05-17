"""Tabbed main window — Inbox · Outbox · New share (v0.4.0 CTk port).

In the v0.3.x Qt version, MainWindow was a QMainWindow + the login was
a separate QDialog. Here, the same ``ctk.CTk`` root we built in
``app.build_root()`` hosts everything: during login it's hidden
(``withdraw``); after sign-in we populate it with the tabview and
show it (``deiconify``)."""
from __future__ import annotations

import tkinter as tk
from typing import Optional

import customtkinter as ctk

from ..api import ApiClient
from ..models import MeResponse
from .settings_dialog import SettingsDialog
from .share_detail_dialog import ShareDetailDialog
from .share_list_panel import ShareListPanel
from .upload_panel import UploadPanel


class MainWindow:
    """Wraps a pre-built ``ctk.CTk`` root + populates it with the three
    tabs. Not itself a widget (the root is the widget) — but exposes
    methods the entry point calls (``show``, etc.) so the rest of the
    code reads naturally."""

    def __init__(self, root: ctk.CTk, api: ApiClient, me: MeResponse) -> None:
        from .. import __version__
        self._version = __version__
        self._app_root = root
        self._api = api
        self._me = me
        self._on_signed_out: Optional[callable] = None
        root.title(
            f"file:Heron — {me.display_name} ({me.role})  ·  v{self._version}"
        )
        # v0.4.27: removed the tk.Menu menu bar. On Windows the menu
        # bar strip is a Win32 control that ignores DWM dark-mode and
        # stayed light below the (now dark) title bar — broke the
        # visual. Settings moved to a ⚙ button in the top-right of
        # the central area; Quit is the window close button.
        self._build_central()

    def _build_central(self) -> None:
        # Top bar with right-aligned ⚙ Settings button (replaces the
        # native menu bar that didn't honour dark mode on Windows).
        top_bar = ctk.CTkFrame(self._app_root, fg_color="transparent")
        top_bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkButton(
            top_bar, text="⚙  Settings", command=self._open_settings,
            width=110, height=28, fg_color="transparent",
            border_width=1, hover_color=("gray85", "gray25"),
        ).pack(side="right")

        self.tabs = ctk.CTkTabview(self._app_root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)

        inbox_tab = self.tabs.add("Inbox")
        outbox_tab = self.tabs.add("Outbox")
        upload_tab = self.tabs.add("New share")

        self.inbox = ShareListPanel(
            inbox_tab, self._app_root, self._api, box="inbox",
            on_open_share=self._open_share,
        )
        self.inbox.pack(fill="both", expand=True)

        self.outbox = ShareListPanel(
            outbox_tab, self._app_root, self._api, box="outbox",
            on_open_share=self._open_share,
        )
        self.outbox.pack(fill="both", expand=True)

        self.upload = UploadPanel(upload_tab, self._app_root, self._api)
        self.upload.pack(fill="both", expand=True)

        # CTkTabview's tab change callback. Refresh the active list
        # panel so newly-created/expired shares show up without a
        # manual click. CTk's API surface is a bit awkward — the
        # tab-change signal is fired via a configure of the segmented
        # button — we poll via a simple StringVar trace.
        self._active_tab = tk.StringVar(value="Inbox")
        # v0.4.22: WRAP CTk's segmented-button command instead of
        # overwriting it. Earlier code just did
        #     self.tabs._segmented_button.configure(command=our_cb)
        # which replaced CTk's internal callback. That callback is the
        # thing that actually SHOWS the right tab frame — without it,
        # clicking a tab updates the highlight but the visible content
        # never swaps. Inbox stayed on screen forever; the "New share"
        # tab looked empty because its frame was never gridded in.
        try:
            original_cb = self.tabs._segmented_button.cget("command")

            def _tab_cb(name: str, _orig=original_cb) -> None:
                if callable(_orig):
                    _orig(name)  # CTk's frame swap
                self._on_tab_changed(name)  # our refresh

            self.tabs._segmented_button.configure(command=_tab_cb)
        except AttributeError:
            # Older CTk versions: skip the auto-refresh; user can press
            # the in-panel Refresh button.
            pass

    def _on_tab_changed(self, name: str) -> None:
        if name == "Inbox":
            self.inbox.refresh()
        elif name == "Outbox":
            self.outbox.refresh()

    def _open_share(self, share_id: str) -> None:
        dlg = ShareDetailDialog(
            self._app_root,
            self._api,
            share_id,
            self._me,
            on_mutated=self._refresh_current_tab,
        )
        dlg.show_modal()

    def _refresh_current_tab(self) -> None:
        name = self.tabs.get()
        if name == "Inbox":
            self.inbox.refresh()
        elif name == "Outbox":
            self.outbox.refresh()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(
            self._app_root, self._api, self._me,
            on_signed_out=self._handle_signed_out,
        )
        dlg.show_modal()

    def _handle_signed_out(self) -> None:
        # Caller in __main__ may want to know — bubble up if registered.
        if self._on_signed_out is not None:
            self._on_signed_out()
        self._app_root.destroy()

    def set_on_signed_out(self, callback) -> None:
        self._on_signed_out = callback

    def show(self) -> None:
        # v0.4.13 traced the symptom: CTk's _windows_set_titlebar_color
        # runs withdraw() → DWM call → deiconify() on Windows, and the
        # deiconify gets lost when it lands mid-MainWindow-construction.
        # The root stays withdrawn forever. v0.4.14 tried to disable
        # CTk's routine and broke the login window. v0.4.15 keeps the
        # routine alive but polls root.state() for 3 seconds after
        # show() and re-deiconifies any time it's not 'normal'.
        # v0.4.17 tried to patch the routine out — broke first-show.
        # v0.4.18 reverted to v0.4.15's polling: known-working, but
        # the polling races with CTk's DWM call so the title bar
        # stays light even in dark mode. Accepted regression.
        self._app_root.deiconify()
        self._app_root.lift()
        self._app_root.focus_force()
        # Aggressive safety net — poll every 50ms for 3s, force back
        # to normal if anything withdraws us.
        self._reassert_visible(remaining_ticks=60)
        # Kick the first list load — without it the user sees empty
        # tabs until they click around.
        self.inbox.refresh()

    def _reassert_visible(self, remaining_ticks: int) -> None:
        try:
            state = self._app_root.state()
            if state != "normal":
                self._app_root.deiconify()
                self._app_root.lift()
        except Exception:
            return
        if remaining_ticks > 0:
            self._app_root.after(
                50, lambda: self._reassert_visible(remaining_ticks - 1)
            )
