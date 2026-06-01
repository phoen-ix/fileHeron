"""Settings dialog (v0.4.0 CTk port): server URL display, current
account, appearance-mode picker, sign-out."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import __version__
from ..api import ApiClient
from ..config import load_config, save_config
from ..i18n import get_locale, set_locale, t
from ..models import MeResponse
from .app import center_window, set_appearance_mode
from . import _messagebox as mb


class SettingsDialog:
    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        on_signed_out: Optional[Callable[[], None]] = None,
    ) -> None:
        self._app_root = root
        self._api = api
        self._me = me
        self._on_signed_out = on_signed_out
        self._cfg = load_config()
        self._win = ctk.CTkToplevel(root)
        self._win.title(t("settings.title"))
        center_window(self._win, 440, 470)
        self._win.resizable(False, False)
        self._win.transient(root)
        self._build()

    # Map for the Language picker: display label → server-side
    # locale code. "Auto" passes the user's existing users.locale
    # through; the SPA's equivalent dropdown uses the same shape.
    _LOCALE_LABELS = (
        ("settings.language_auto", ""),
        ("settings.language_en", "en"),
        ("settings.language_de", "de"),
    )

    def _build(self) -> None:
        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        # Two-column "label : value" grid.
        rows = [
            (t("settings.row_server"), self._api.server_url),
            (t("settings.row_account"), f"{self._me.display_name}  ({self._me.email})"),
            (t("settings.row_role"), self._me.role),
            (t("settings.row_version"), __version__),
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
        appearance_row = len(rows)
        ctk.CTkLabel(outer, text=t("settings.appearance"), anchor="w").grid(
            row=appearance_row, column=0, sticky="w", padx=(0, 12), pady=(12, 4)
        )
        self._appearance_var = ctk.StringVar(value=ctk.get_appearance_mode())
        self._appearance_labels = {
            "Light": t("settings.appearance_light"),
            "Dark": t("settings.appearance_dark"),
            "System": t("settings.appearance_system"),
        }
        ctk.CTkOptionMenu(
            outer,
            variable=self._appearance_var,
            values=list(self._appearance_labels.values()),
            command=self._on_appearance,
            width=140,
        ).grid(row=appearance_row, column=1, sticky="w", pady=(12, 4))
        # Display the CURRENT appearance label in the active locale.
        self._appearance_var.set(
            self._appearance_labels.get(
                ctk.get_appearance_mode(),
                self._appearance_labels["System"],
            )
        )

        # Language picker (v0.8.0). Calls patch_locale → updates the
        # active i18n locale + persists to users.locale. The current
        # locale doesn't auto-translate the open dialog (would require
        # a full re-pack); takes effect on the next dialog/window open.
        lang_row = appearance_row + 1
        ctk.CTkLabel(outer, text=t("settings.language"), anchor="w").grid(
            row=lang_row, column=0, sticky="w", padx=(0, 12), pady=(12, 4),
        )
        self._lang_label_to_code = {t(k): v for (k, v) in self._LOCALE_LABELS}
        self._lang_code_to_label = {v: k for (k, v) in self._lang_label_to_code.items()}
        current_code = (self._me.locale or "").lower()
        current_label = self._lang_code_to_label.get(current_code, t("settings.language_auto"))
        self._lang_var = ctk.StringVar(value=current_label)
        ctk.CTkOptionMenu(
            outer,
            variable=self._lang_var,
            values=list(self._lang_label_to_code.keys()),
            command=self._on_language,
            width=200,
        ).grid(row=lang_row, column=1, sticky="w", pady=(12, 4))

        # Diagnostic logging toggle (v0.4.16). Default OFF — the verbose
        # trace.log + app.log + heartbeat plumbing only fires when this
        # is on. crash.log (uncaught exceptions + native faulthandler)
        # writes either way. Effect on next launch — loggers are wired
        # at startup.
        diag_row = lang_row + 1
        ctk.CTkLabel(outer, text=t("settings.diagnostics"), anchor="w").grid(
            row=diag_row, column=0, sticky="nw", padx=(0, 12), pady=(12, 4)
        )
        diag_cell = ctk.CTkFrame(outer, fg_color="transparent")
        diag_cell.grid(row=diag_row, column=1, sticky="w", pady=(12, 4))
        self._diag_switch = ctk.CTkSwitch(
            diag_cell,
            text=t("settings.diagnostics_switch"),
            command=self._on_diag_toggled,
        )
        self._diag_switch.pack(anchor="w")
        if self._cfg.enable_diagnostic_logging:
            self._diag_switch.select()
        ctk.CTkLabel(
            diag_cell,
            text=t("settings.diagnostics_help"),
            text_color="gray",
            anchor="w",
        ).pack(anchor="w")

        # Buttons row, anchored to the bottom of the dialog.
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.grid(row=diag_row + 1, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        ctk.CTkButton(btn_row, text=t("common.close"), command=self._win.destroy, width=90).pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("settings.sign_out"), command=self._on_sign_out, width=90,
            fg_color="#991b1b", hover_color="#7f1d1d",
        ).pack(side="right", padx=(0, 8))

    def _on_appearance(self, mode: str) -> None:
        # The picker now shows translated labels; map back to the CTk
        # canonical English value before passing through.
        canonical = next(
            (k for (k, v) in self._appearance_labels.items() if v == mode),
            "System",
        )
        set_appearance_mode(canonical.lower())

    def _on_language(self, label: str) -> None:
        """v0.8.0: persist the picked locale to users.locale and apply
        it immediately in the running app. The Settings dialog itself
        doesn't repaint — labels update on next dialog open. The fall-
        back to local-only set_locale on a failed PATCH keeps the UI
        usable when the server is unreachable."""
        from .. import api as api_pkg

        code = self._lang_label_to_code.get(label, "")
        applied = code or (self._me.locale or "en")
        # Apply locally first so the user sees the change without a
        # round-trip wait.
        set_locale(applied)
        try:
            updated = api_pkg.patch_locale(self._api, applied)
            self._me = updated
        except Exception as exc:
            mb.warn(
                self._win,
                t("common.error"),
                t("settings.language_save_failed",
                  detail=getattr(exc, "message", None) or str(exc),
                  locale=applied),
            )

    def _on_diag_toggled(self) -> None:
        self._cfg.enable_diagnostic_logging = bool(self._diag_switch.get())
        try:
            save_config(self._cfg)
        except Exception:
            # Don't crash the settings dialog if config save fails;
            # crash.log will capture if it's something serious.
            pass

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
