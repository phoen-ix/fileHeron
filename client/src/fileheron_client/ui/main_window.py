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
        self._root = root
        self._api = api
        self._me = me
        self._on_signed_out: Optional[callable] = None
        root.title(f"file:Heron — {me.display_name} ({me.role})")
        self._build_menu()
        self._build_central()

    def _build_menu(self) -> None:
        # tkinter.Menu is stdlib — CTk doesn't ship a menu widget.
        # The native menu look is acceptable since it's just two items.
        menubar = tk.Menu(self._root)
        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="Settings…", command=self._open_settings)
        m_file.add_separator()
        m_file.add_command(label="Quit", command=self._root.destroy)
        menubar.add_cascade(label="File", menu=m_file)
        self._root.config(menu=menubar)

    def _build_central(self) -> None:
        self.tabs = ctk.CTkTabview(self._root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)

        inbox_tab = self.tabs.add("Inbox")
        outbox_tab = self.tabs.add("Outbox")
        upload_tab = self.tabs.add("New share")

        self.inbox = ShareListPanel(
            inbox_tab, self._root, self._api, box="inbox",
            on_open_share=self._open_share,
        )
        self.inbox.pack(fill="both", expand=True)

        self.outbox = ShareListPanel(
            outbox_tab, self._root, self._api, box="outbox",
            on_open_share=self._open_share,
        )
        self.outbox.pack(fill="both", expand=True)

        self.upload = UploadPanel(upload_tab, self._root, self._api)
        self.upload.pack(fill="both", expand=True)

        # CTkTabview's tab change callback. Refresh the active list
        # panel so newly-created/expired shares show up without a
        # manual click. CTk's API surface is a bit awkward — the
        # tab-change signal is fired via a configure of the segmented
        # button — we poll via a simple StringVar trace.
        self._active_tab = tk.StringVar(value="Inbox")
        # CTk 5.x exposes ``set`` and ``get`` on CTkTabview; wire up
        # a callback on the underlying segmented button.
        try:
            self.tabs._segmented_button.configure(command=self._on_tab_changed)
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
            self._root,
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
            self._root, self._api, self._me,
            on_signed_out=self._handle_signed_out,
        )
        dlg.show_modal()

    def _handle_signed_out(self) -> None:
        # Caller in __main__ may want to know — bubble up if registered.
        if self._on_signed_out is not None:
            self._on_signed_out()
        self._root.destroy()

    def set_on_signed_out(self, callback) -> None:
        self._on_signed_out = callback

    def show(self) -> None:
        # Called by __main__ after a successful login. The root was
        # hidden during the login phase.
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()
        # Kick the first list load — without it the user sees empty
        # tabs until they click around.
        self.inbox.refresh()
