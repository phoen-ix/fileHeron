"""Tabbed main window - Inbox · Outbox · New share (v0.4.0 CTk port).

In the v0.3.x Qt version, MainWindow was a QMainWindow + the login was
a separate QDialog. Here, the same ``ctk.CTk`` root we built in
``app.build_root()`` hosts everything. v0.9.1: the root is visible from
startup with a ``LoginOverlay`` placed on top; after sign-in the
``AppController`` constructs this window into the root and removes the
overlay, and ``teardown()`` reverses it on sign-out / session-expiry (the
app no longer quits on logout)."""
from __future__ import annotations

import tkinter as tk
from typing import Optional

import customtkinter as ctk

from ..api import ApiClient
from ..i18n import t
from ..models import MeResponse
from .app import reassert_visible
from .settings_dialog import SettingsOverlay
from .share_detail_view import pause_all_in_flight
from .share_list_panel import ShareListPanel
from .upload_panel import UploadPanel
from .widgets import Toast

# Tab keys - the lookup keys CTk uses for tab switching and that
# ``_on_tab_changed`` matches against. The displayed label is the same
# string for now (localised labels would require remapping the segmented
# button text without changing the lookup key).
TAB_INBOX = "Inbox"
TAB_OUTBOX = "Outbox"
TAB_NEW_SHARE = "New share"


class MainWindow:
    """Wraps a pre-built ``ctk.CTk`` root + populates it with the three
    tabs. Not itself a widget (the root is the widget) - but exposes
    methods the entry point calls (``show``, etc.) so the rest of the
    code reads naturally."""

    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        on_signed_out: Optional[callable] = None,
    ) -> None:
        from .. import __version__
        self._version = __version__
        self._app_root = root
        self._api = api
        self._me = me
        self._on_signed_out = on_signed_out
        self._settings_overlay: Optional[SettingsOverlay] = None
        self._toast: Optional[Toast] = None
        # Branding logo (admin-optional). Populated post-login by
        # _load_branding_logo; refs held to survive Tk image GC.
        self._logo_label: Optional[ctk.CTkLabel] = None
        self._logo_image: Optional[tk.PhotoImage] = None
        root.title(
            t("app.title_template",
              name=me.display_name, role=me.role, version=self._version)
        )
        # v0.4.27: removed the tk.Menu menu bar. On Windows the menu
        # bar strip is a Win32 control that ignores DWM dark-mode and
        # stayed light below the (now dark) title bar - broke the
        # visual. Settings moved to a ⚙ button in the top-right of
        # the central area; Quit is the window close button.
        self._build_central()

    def _build_central(self) -> None:
        # Top bar with right-aligned ⚙ Settings button (replaces the
        # native menu bar that didn't honour dark mode on Windows).
        # Held on self so teardown() can destroy it on sign-out.
        self._top_bar = ctk.CTkFrame(self._app_root, fg_color="transparent")
        self._top_bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkButton(
            self._top_bar, text=t("main_window.settings_button"),
            command=self._open_settings,
            width=110, height=28, fg_color="transparent",
            border_width=1, hover_color=("gray85", "gray25"),
        ).pack(side="right")

        self.tabs = ctk.CTkTabview(self._app_root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)

        # Non-modal toast for info/success notifications (replaces the
        # interrupting messagebox popups). Parents to the root so any tab /
        # the drilled-in detail view can reach it via the flash callback.
        self._toast = Toast(self._app_root)

        inbox_tab = self.tabs.add(TAB_INBOX)
        outbox_tab = self.tabs.add(TAB_OUTBOX)
        upload_tab = self.tabs.add(TAB_NEW_SHARE)

        self.inbox = ShareListPanel(
            inbox_tab, self._app_root, self._api, self._me, box="inbox",
            flash=self.flash,
        )
        self.inbox.pack(fill="both", expand=True)

        self.outbox = ShareListPanel(
            outbox_tab, self._app_root, self._api, self._me, box="outbox",
            flash=self.flash,
        )
        self.outbox.pack(fill="both", expand=True)

        self.upload = UploadPanel(
            upload_tab, self._app_root, self._api, self._me,
            flash=self.flash, on_view_outbox=self._go_to_outbox,
        )
        self.upload.pack(fill="both", expand=True)

        # CTkTabview's tab change callback. Refresh the active list
        # panel so newly-created/expired shares show up without a
        # manual click. CTk's API surface is a bit awkward - the
        # tab-change signal is fired via a configure of the segmented
        # button - we poll via a simple StringVar trace.
        self._active_tab = tk.StringVar(value=TAB_INBOX)
        # v0.4.22: WRAP CTk's segmented-button command instead of
        # overwriting it. Earlier code just did
        #     self.tabs._segmented_button.configure(command=our_cb)
        # which replaced CTk's internal callback. That callback is the
        # thing that actually SHOWS the right tab frame - without it,
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
        if name == TAB_INBOX:
            self.inbox.refresh()
        elif name == TAB_OUTBOX:
            self.outbox.refresh()

    def _go_to_outbox(self) -> None:
        """Switch to the Outbox tab + refresh - wired into the upload-progress
        view's 'View in Outbox' action. Uses the public CTkTabview.set (not the
        wrapped _segmented_button command); .set doesn't fire that wrapped
        auto-refresh callback, so refresh explicitly."""
        self.tabs.set(TAB_OUTBOX)
        self.outbox.refresh()

    def _open_settings(self) -> None:
        if self._settings_overlay is not None:
            return  # already open
        self._settings_overlay = SettingsOverlay(
            self._app_root, self._api, self._me,
            on_signed_out=self._on_signed_out,
            on_closed=self._on_settings_closed,
        )
        self._settings_overlay.show()

    def _on_settings_closed(self) -> None:
        self._settings_overlay = None

    def flash(self, text: str, kind: str = "info") -> None:
        """Show a transient, non-modal toast. Threaded into the panels as the
        `flash=` callback so they notify without a popup."""
        if self._toast is not None:
            self._toast.show(text, kind=kind)

    def post_show(self) -> None:
        """Called by AppController after the overlay is removed. The root is
        already visible (no withdraw in the overlay era), so this just brings
        the tabs to front, runs the titlebar-withdraw safety net, and kicks
        the first list load so the user doesn't see empty tabs."""
        self._app_root.lift()
        # Safety net - CTk's _windows_set_titlebar_color can withdraw the
        # window on Windows; poll for 3s and force it back to normal.
        reassert_visible(self._app_root, 60)
        self.inbox.refresh()
        self._load_branding_logo()

    def _load_branding_logo(self) -> None:
        """Fetch the admin branding logo (if any) in the background and place it
        at the left of the top bar. Best-effort - 404/error just leaves it
        blank. The server returns a PNG sized for the header, so the client
        needs no image library or resizing (Tk PhotoImage renders PNG)."""
        from ..api.branding import branding_logo_png
        from ._async import run_in_background

        api = self._api

        def _fetch():
            return branding_logo_png(api)

        run_in_background(self._app_root, _fetch, on_done=self._apply_branding_logo)

    def _apply_branding_logo(self, png_bytes) -> None:
        if not png_bytes:
            return
        import base64
        try:
            img = tk.PhotoImage(data=base64.b64encode(png_bytes).decode("ascii"))
        except Exception:
            return
        # _top_bar may be gone if the user signed out during the fetch.
        try:
            self._logo_image = img  # keep a ref so Tk doesn't GC it
            self._logo_label = ctk.CTkLabel(self._top_bar, image=img, text="")
            self._logo_label.pack(side="left", padx=(2, 8))
        except Exception:
            self._logo_image = None
            self._logo_label = None

    def teardown(self) -> None:
        """Destroy the main UI (top bar + tabview) on sign-out / session
        expiry so the AppController can place a fresh login overlay on a
        clean root. Destroying the tabview recursively destroys both share
        panels, the upload panel, any drilled-in detail view, and the
        wrapped segmented-button callback (all descendants of self.tabs)."""
        # Downloads still running for this session keep their partials: pause
        # them, so the registry offers Resume next time, rather than let the
        # workers write on behind a screen that no longer exists.
        pause_all_in_flight()
        # Settings overlay + toast parent to the root, not self.tabs, so they
        # survive a tabview destroy - tear them down explicitly.
        for w in (self._settings_overlay, self._toast):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self._settings_overlay = None
        self._toast = None
        try:
            self._top_bar.destroy()
        except Exception:
            pass
        # The logo label is a child of _top_bar (destroyed above); drop the
        # image ref so Tk can GC it.
        self._logo_label = None
        self._logo_image = None
        try:
            self.tabs.destroy()
        except Exception:
            pass
        try:
            self._app_root.title("file:Heron")
        except Exception:
            pass
