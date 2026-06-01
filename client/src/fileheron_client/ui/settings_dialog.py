"""Settings — in-window overlay (v0.9.4).

Was a ``CTkToplevel`` modal; now a ``CTkFrame`` placed full-cover over the root
(dimmed backdrop + centered card), the same pattern as ``LoginOverlay`` — no
popup. Content (server/account/role/version rows, appearance + language pickers,
diagnostics toggle, sign-out) and its handlers are unchanged; only the container
and the close mechanism (place_forget/destroy instead of wait_window) differ."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import __version__
from ..api import ApiClient
from ..config import load_config, save_config
from ..i18n import set_locale, t
from ..models import MeResponse
from .app import set_appearance_mode


class SettingsOverlay(ctk.CTkFrame):
    def __init__(
        self,
        root: ctk.CTk,
        api: ApiClient,
        me: MeResponse,
        *,
        on_signed_out: Optional[Callable[[], None]] = None,
        on_closed: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(root, fg_color=("gray75", "gray10"))
        self._app_root = root
        self._api = api
        self._me = me
        self._on_signed_out = on_signed_out
        self._on_closed = on_closed
        self._cfg = load_config()
        self._esc_targets: list = []
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
        card = ctk.CTkFrame(self, fg_color=("gray92", "gray16"), corner_radius=12)
        card.place(relx=0.5, rely=0.5, anchor="center")
        outer = ctk.CTkFrame(card, fg_color="transparent", width=400)
        outer.pack(fill="both", expand=True, padx=22, pady=20)

        ctk.CTkLabel(
            outer, text=t("settings.title"),
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Two-column "label : value" grid.
        rows = [
            (t("settings.row_server"), self._api.server_url),
            (t("settings.row_account"), f"{self._me.display_name}  ({self._me.email})"),
            (t("settings.row_role"), self._me.role),
            (t("settings.row_version"), __version__),
        ]
        base = 1
        for i, (label, value) in enumerate(rows):
            r = base + i
            ctk.CTkLabel(outer, text=label, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=4
            )
            ctk.CTkLabel(outer, text=value, anchor="w", text_color="gray").grid(
                row=r, column=1, sticky="ew", pady=4
            )

        outer.grid_columnconfigure(1, weight=1)

        # Appearance mode picker (v0.4.0 — CTk's built-in theming).
        appearance_row = base + len(rows)
        ctk.CTkLabel(outer, text=t("settings.appearance"), anchor="w").grid(
            row=appearance_row, column=0, sticky="w", padx=(0, 12), pady=(12, 4)
        )
        self._appearance_var = ctk.StringVar(value=ctk.get_appearance_mode())
        self._appearance_labels = {
            "Light": t("settings.appearance_light"),
            "Dark": t("settings.appearance_dark"),
            "System": t("settings.appearance_system"),
        }
        appearance_menu = ctk.CTkOptionMenu(
            outer,
            variable=self._appearance_var,
            values=list(self._appearance_labels.values()),
            command=self._on_appearance,
            width=140,
        )
        appearance_menu.grid(row=appearance_row, column=1, sticky="w", pady=(12, 4))
        self._esc_targets.append(appearance_menu)
        # Display the CURRENT appearance label in the active locale.
        self._appearance_var.set(
            self._appearance_labels.get(
                ctk.get_appearance_mode(),
                self._appearance_labels["System"],
            )
        )

        # Language picker (v0.8.0). Calls patch_locale → updates the
        # active i18n locale + persists to users.locale. The current
        # locale doesn't auto-translate the open panel (would require
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
        lang_menu = ctk.CTkOptionMenu(
            outer,
            variable=self._lang_var,
            values=list(self._lang_label_to_code.keys()),
            command=self._on_language,
            width=200,
        )
        lang_menu.grid(row=lang_row, column=1, sticky="w", pady=(12, 4))
        self._esc_targets.append(lang_menu)

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
        self._esc_targets.append(self._diag_switch)
        if self._cfg.enable_diagnostic_logging:
            self._diag_switch.select()
        ctk.CTkLabel(
            diag_cell,
            text=t("settings.diagnostics_help"),
            text_color="gray",
            anchor="w",
        ).pack(anchor="w")

        # Buttons row.
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.grid(row=diag_row + 1, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        self._close_btn = ctk.CTkButton(
            btn_row, text=t("common.close"), command=self._close, width=90,
        )
        self._close_btn.pack(side="right")
        signout_btn = ctk.CTkButton(
            btn_row, text=t("settings.sign_out"), command=self._on_sign_out, width=90,
            fg_color="#991b1b", hover_color="#7f1d1d",
        )
        signout_btn.pack(side="right", padx=(0, 8))
        self._esc_targets.extend([self._close_btn, signout_btn])

        # Inline status line (e.g. a failed language save) — no popup.
        self._status_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            outer, textvariable=self._status_var, text_color="#991b1b",
            anchor="w", wraplength=380, justify="left",
        ).grid(row=diag_row + 2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        # Esc closes. No grab_set, so bind on each focusable control.
        for w in self._esc_targets:
            w.bind("<Escape>", lambda _e: self._close())

    # ---- show / hide -----------------------------------------------------

    def show(self) -> None:
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self.after_idle(lambda: self._close_btn.focus_set())

    def hide(self) -> None:
        try:
            self.place_forget()
        except Exception:
            pass

    def _close(self) -> None:
        self.hide()
        try:
            self.destroy()
        except Exception:
            pass
        if self._on_closed is not None:
            self._on_closed()

    # ---- handlers (unchanged logic) --------------------------------------

    def _on_appearance(self, mode: str) -> None:
        # The picker shows translated labels; map back to the CTk
        # canonical English value before passing through.
        canonical = next(
            (k for (k, v) in self._appearance_labels.items() if v == mode),
            "System",
        )
        set_appearance_mode(canonical.lower())

    def _on_language(self, label: str) -> None:
        """v0.8.0: persist the picked locale to users.locale and apply
        it immediately in the running app. The panel itself doesn't
        repaint — labels update on next open. The fallback to local-only
        set_locale on a failed PATCH keeps the UI usable when the server
        is unreachable."""
        from .. import api as api_pkg

        code = self._lang_label_to_code.get(label, "")
        applied = code or (self._me.locale or "en")
        set_locale(applied)
        self._status_var.set("")
        try:
            updated = api_pkg.patch_locale(self._api, applied)
            self._me = updated
        except Exception as exc:
            self._status_var.set(
                t("settings.language_save_failed",
                  detail=getattr(exc, "message", None) or str(exc),
                  locale=applied)
            )

    def _on_diag_toggled(self) -> None:
        self._cfg.enable_diagnostic_logging = bool(self._diag_switch.get())
        try:
            save_config(self._cfg)
        except Exception:
            # Don't crash on a config-save failure; crash.log captures
            # anything serious.
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
        # Remove this overlay first, then hand off to the controller (which
        # tears down the main window and shows a fresh login overlay).
        self._close()
        if self._on_signed_out is not None:
            self._on_signed_out()
