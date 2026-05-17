"""Settings dialog (v0.4.0 CTk port): server URL display, current
account, appearance-mode picker, sign-out."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import __version__
from ..api import ApiClient
from ..models import MeResponse
from .app import set_appearance_mode


class SettingsDialog:
    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        on_signed_out: Optional[Callable[[], None]] = None,
    ) -> None:
        self._root = root
        self._api = api
        self._me = me
        self._on_signed_out = on_signed_out
        self._win = ctk.CTkToplevel(root)
        self._win.title("Settings")
        self._win.geometry("420x320")
        self._win.resizable(False, False)
        self._win.transient(root)
        self._build()

    def _build(self) -> None:
        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        # Two-column "label : value" grid.
        rows = [
            ("Server", self._api.server_url),
            ("Account", f"{self._me.display_name}  ({self._me.email})"),
            ("Role", self._me.role),
            ("Client version", __version__),
        ]
        for r, (label, value) in enumerate(rows):
            ctk.CTkLabel(outer, text=label, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=4
            )
            ctk.CTkLabel(outer, text=value, anchor="w", text_color="gray").grid(
                row=r, column=1, sticky="ew", pady=4
            )

        outer.grid_columnconfigure(1, weight=1)

        # Appearance mode picker (v0.4.0 — CTk's built-in theming).
        ctk.CTkLabel(outer, text="Appearance", anchor="w").grid(
            row=len(rows), column=0, sticky="w", padx=(0, 12), pady=(12, 4)
        )
        self._appearance_var = ctk.StringVar(value=ctk.get_appearance_mode())
        ctk.CTkOptionMenu(
            outer,
            variable=self._appearance_var,
            values=["Light", "Dark", "System"],
            command=self._on_appearance,
            width=140,
        ).grid(row=len(rows), column=1, sticky="w", pady=(12, 4))

        # Buttons row, anchored to the bottom of the dialog.
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        ctk.CTkButton(btn_row, text="Close", command=self._win.destroy, width=90).pack(side="right")
        ctk.CTkButton(
            btn_row, text="Sign out", command=self._on_sign_out, width=90,
            fg_color="#991b1b", hover_color="#7f1d1d",
        ).pack(side="right", padx=(0, 8))

    def _on_appearance(self, mode: str) -> None:
        # CTk's API accepts lowercase strings.
        set_appearance_mode(mode.lower())

    def _on_sign_out(self) -> None:
        from .. import api as api_pkg
        from ..config import clear_secret

        try:
            api_pkg.logout(self._api)
        except Exception:
            pass
        clear_secret("refresh", self._api.server_url)
        clear_secret("api_token", self._api.server_url)
        if self._on_signed_out is not None:
            self._on_signed_out()
        self._win.destroy()

    def show_modal(self) -> None:
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))
        self._win.wait_window()
