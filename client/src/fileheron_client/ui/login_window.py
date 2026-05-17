"""Login dialog: server URL + email/password (with optional TOTP) OR
API token. Calls back with the wired-up ApiClient + MeResponse on
success. v0.4.0 CustomTkinter port."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from .._trace import trace
from ..api import ApiClient, ApiError
from ..config import (
    ClientConfig,
    get_secret,
    save_config,
    set_secret,
)
from ._async import run_in_background


class LoginWindow:
    """Modal sign-in window with two interchangeable auth modes
    (password / api token). Calls ``on_signed_in(api, me)`` on success.

    Not a CTk widget itself — wraps a ``CTkToplevel`` parented to the
    hidden root so we can ``wait_window`` to block ``main()`` until the
    user signs in or cancels.
    """

    def __init__(
        self,
        root: ctk.CTk,
        cfg: ClientConfig,
        on_signed_in: Callable[[ApiClient, object], None],
    ) -> None:
        self._app_root = root
        self._cfg = cfg
        self._on_signed_in = on_signed_in
        from .. import __version__
        self._win = ctk.CTkToplevel(root)
        # Version in the title bar so we can identify which build the
        # user is running even when they can't reach Settings (e.g. a
        # sign-in error blocks them).
        self._win.title(f"Sign in to file:Heron  —  client v{__version__}")
        self._win.geometry("480x460")
        self._win.resizable(False, False)
        # We're shown while the root is hidden; without transient() the
        # window doesn't always lift. transient(self._app_root) is harmless.
        self._win.transient(root)
        self._build()
        self._show_mode(self._cfg.auth_kind)

    def _build(self) -> None:
        outer = ctk.CTkFrame(self._win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        intro = ctk.CTkLabel(
            outer,
            text=(
                "Connect to a file:Heron server. Your credentials live in "
                "your OS credential store; only the server URL is saved on disk."
            ),
            wraplength=420,
            justify="left",
        )
        intro.pack(fill="x", pady=(0, 12))

        # Server URL — common to both modes.
        ctk.CTkLabel(outer, text="Server URL", anchor="w").pack(fill="x")
        self.server_url_var = ctk.StringVar(value=self._cfg.server_url)
        ctk.CTkEntry(
            outer, textvariable=self.server_url_var, placeholder_text="https://files.example.com"
        ).pack(fill="x", pady=(0, 12))

        # Two stacked frames; only one visible at a time.
        self._pw_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._tok_frame = ctk.CTkFrame(outer, fg_color="transparent")

        # Password mode
        ctk.CTkLabel(self._pw_frame, text="Email", anchor="w").pack(fill="x")
        self.email_var = ctk.StringVar(value=self._cfg.last_email or "")
        ctk.CTkEntry(self._pw_frame, textvariable=self.email_var).pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(self._pw_frame, text="Password", anchor="w").pack(fill="x")
        self.password_var = ctk.StringVar()
        ctk.CTkEntry(self._pw_frame, textvariable=self.password_var, show="*").pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(self._pw_frame, text="TOTP (only if asked)", anchor="w").pack(fill="x")
        self.totp_var = ctk.StringVar()
        ctk.CTkEntry(self._pw_frame, textvariable=self.totp_var, placeholder_text="6-digit code").pack(fill="x", pady=(0, 8))

        # API-token mode
        ctk.CTkLabel(self._tok_frame, text="API token", anchor="w").pack(fill="x")
        self.api_token_var = ctk.StringVar()
        ctk.CTkEntry(
            self._tok_frame,
            textvariable=self.api_token_var,
            show="*",
            placeholder_text="fh_xxxxxxxx_…  (from /account/api-tokens)",
        ).pack(fill="x", pady=(0, 8))

        # Toggle row — CTk-style hyperlink (just a label that calls
        # us on click).
        toggle_row = ctk.CTkFrame(outer, fg_color="transparent")
        toggle_row.pack(fill="x", pady=(4, 8))
        self.toggle_label = ctk.CTkLabel(
            toggle_row,
            text="Use API token instead",
            text_color=("#1e6fbf", "#5fa8ff"),
            cursor="hand2",
        )
        self.toggle_label.pack(side="left")
        self.toggle_label.bind("<Button-1>", self._on_toggle)

        # Error label — hidden until something goes wrong.
        self.error_var = ctk.StringVar(value="")
        self.error_label = ctk.CTkLabel(
            outer, textvariable=self.error_var, text_color="#991b1b", wraplength=420, justify="left"
        )
        self.error_label.pack(fill="x", pady=(0, 8))

        # Buttons
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x")
        self.signin_btn = ctk.CTkButton(btn_row, text="Sign in", command=self._on_signin)
        self.signin_btn.pack(side="right")
        ctk.CTkButton(
            btn_row, text="Cancel", command=self._win.destroy, fg_color="gray"
        ).pack(side="right", padx=(0, 8))

        # Enter submits.
        self._win.bind("<Return>", lambda _e: self._on_signin())
        self._win.bind("<Escape>", lambda _e: self._win.destroy())

    def _show_mode(self, kind: str) -> None:
        # Hide both, show the requested one.
        self._pw_frame.pack_forget()
        self._tok_frame.pack_forget()
        if kind == "api_token":
            self._tok_frame.pack(fill="x", pady=(0, 0), before=self.toggle_label.master)
            self.toggle_label.configure(text="Use email + password instead")
            # Pre-fill from keyring if available.
            stored = get_secret("api_token", self.server_url_var.get().rstrip("/"))
            if stored:
                self.api_token_var.set(stored)
        else:
            self._pw_frame.pack(fill="x", pady=(0, 0), before=self.toggle_label.master)
            self.toggle_label.configure(text="Use API token instead")

    def _on_toggle(self, _event=None) -> None:
        current_kind = self._cfg.auth_kind
        self._cfg.auth_kind = "api_token" if current_kind != "api_token" else "password"
        self._show_mode(self._cfg.auth_kind)

    def _on_signin(self) -> None:
        self.error_var.set("")
        # v0.4.3: snapshot ALL Tk variables on the main thread BEFORE
        # spawning the worker. Reading StringVar.get() from a worker
        # thread is unsupported by Tk — on Windows it deadlocks the
        # worker because Tcl's interpreter lock is held by the main
        # thread sitting in the event loop. v0.4.2 read them from
        # _attempt() which is exactly that.
        server = self.server_url_var.get().strip().rstrip("/")
        email = self.email_var.get().strip()
        password = self.password_var.get()
        totp = self.totp_var.get().strip() or None
        api_token = self.api_token_var.get().strip()
        if not server:
            self._show_error("Server URL is required.")
            return
        kind = self._cfg.auth_kind
        if kind == "password" and not email:
            self._show_error("Email is required.")
            return
        if kind == "password" and not password:
            self._show_error("Password is required.")
            return
        if kind == "api_token" and not api_token:
            self._show_error("API token is required.")
            return

        # Run the network call in a background thread so the UI stays
        # responsive while we hit /api/account/me. The button is
        # disabled during the in-flight call.
        self.signin_btn.configure(state="disabled", text="Signing in…")

        def _attempt():
            trace(f"_attempt start (kind={kind}, server={server})")
            if kind == "api_token":
                api = ApiClient(server, api_token=api_token)
                me = api_pkg.me(api)
                set_secret("api_token", server, api_token)
                trace("_attempt done (api_token)")
                return api, me, kind
            api = ApiClient(server)
            trace("calling api_pkg.login")
            api_pkg.login(
                api, email=email, password=password, totp_code=totp,
            )
            trace("login OK; calling api_pkg.me")
            me = api_pkg.me(api)
            trace("_attempt done (password)")
            return api, me, kind

        def _done(result):
            trace("_done callback fired (on main thread)")
            api, me, used_kind = result
            self._cfg.server_url = server
            self._cfg.auth_kind = used_kind
            if used_kind == "password":
                self._cfg.last_email = email
            try:
                save_config(self._cfg)
                trace("save_config OK")
            except Exception as exc:
                trace(f"save_config FAILED: {exc!r}")
            # Fire the callback BEFORE destroy so the main window can
            # take over the root before we yield the event loop.
            try:
                trace("invoking _on_signed_in")
                self._on_signed_in(api, me)
                trace("_on_signed_in returned")
            except Exception as exc:
                trace(f"_on_signed_in RAISED: {exc!r}")
                import traceback
                traceback.print_exc()
                self.signin_btn.configure(state="normal", text="Sign in")
                self._show_error(
                    f"Signed in OK but failed to open main window: {exc!r}"
                )
                return
            trace("destroying login window")
            self._win.destroy()
            trace("login window destroyed")

        def _failed(exc):
            trace(f"_failed callback fired: {type(exc).__name__}: {exc!r}")
            self.signin_btn.configure(state="normal", text="Sign in")
            # v0.4.6: write every caught sign-in failure to the crash
            # log with a full Python traceback. Without this the "Could
            # not reach server: …" surface message is all the user can
            # tell us — which isn't enough to debug DLL-load /
            # SSL-import / OS-level failures.
            try:
                import platformdirs
                import traceback
                from datetime import datetime
                from pathlib import Path
                log_path = (
                    Path(platformdirs.user_log_dir("fileHeron", appauthor=False))
                    / "crash.log"
                )
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n--- {datetime.now().isoformat()} [signin] ---\n")
                    traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            except Exception:
                pass  # logging must never crash the UI thread
            if isinstance(exc, ApiError):
                if exc.code == "TOTP_REQUIRED":
                    self._show_error(
                        "Two-factor code required. Enter the 6-digit code from your authenticator."
                    )
                    return
                if exc.code == "INVALID_TOTP":
                    self._show_error("Invalid TOTP code. Try again.")
                    self.totp_var.set("")
                    return
                self._show_error(exc.message or "Sign-in failed.")
                return
            # Network / TLS / DNS.
            self._show_error(f"Could not reach server: {exc}")

        run_in_background(self._app_root, _attempt, on_done=_done, on_failed=_failed)

    def _show_error(self, msg: str) -> None:
        self.error_var.set(msg)

    # ---- modal entry point ----

    def show_modal(self) -> None:
        # grab_set after the window is realised so the geometry is correct.
        self._win.after_idle(lambda: (self._win.grab_set(), self._win.focus_force()))
        self._win.wait_window()
