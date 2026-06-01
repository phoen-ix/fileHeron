"""Login overlay: server URL + email/password (with optional 2FA) OR API token.

v0.9.3: standard two-step second factor. Step 1 collects email + password
only (no code field up front). If the server answers TOTP_REQUIRED we reveal a
single "authentication code" field that accepts EITHER a 6-digit TOTP code or a
recovery code (XXXX-XXXX) and routes to the right endpoint automatically — the
user never has to choose between them.

v0.9.0 made this an in-window overlay (a ``CTkFrame`` placed full-cover over the
single root: a dimmed backdrop + centered card) instead of a separate
``CTkToplevel``; the ``AppController`` owns the overlay⇄main swap. The auth
calls (``api_pkg.login`` / ``login_with_recovery`` / ``me``) are unchanged."""
from __future__ import annotations

import re
from typing import Callable, Optional

import customtkinter as ctk

from .. import api as api_pkg
from .._trace import trace
from ..api import ApiClient, ApiError
from ..config import (
    ClientConfig,
    get_secret,
    normalize_server_url,
    save_config,
    set_secret,
)
from ..i18n import t
from ._async import run_in_background

# A TOTP code is exactly six digits; a recovery code is XXXX-XXXX (letters + a
# hyphen). They never collide, so the code step routes on shape alone.
_TOTP_RE = re.compile(r"^\d{6}$")

_LINK_COLOR = ("#1e6fbf", "#5fa8ff")
_MUTED_COLOR = ("gray35", "gray70")


class LoginOverlay(ctk.CTkFrame):
    """In-window sign-in overlay. Two-step: credentials → (if 2FA) one code
    field that takes a TOTP or recovery code. Calls ``on_signed_in(api, me)``
    on success and ``on_cancel()`` when the user quits.

    Is a ``CTkFrame`` parented to the root — the frame is the dimmed backdrop;
    an inner card holds the form. ``show()``/``hide()`` toggle the full-cover
    ``place``. ``info`` renders a neutral banner above the form (session-expiry
    re-show)."""

    def __init__(
        self,
        root: ctk.CTk,
        cfg: ClientConfig,
        *,
        on_signed_in: Callable[[ApiClient, object], None],
        on_cancel: Callable[[], None],
        info: Optional[str] = None,
    ) -> None:
        super().__init__(root, fg_color=("gray75", "gray10"))
        self._app_root = root
        self._cfg = cfg
        self._on_signed_in = on_signed_in
        self._on_cancel = on_cancel
        self._info = info
        self._step = "creds"  # "creds" | "code"
        self._entries: list[ctk.CTkEntry] = []
        self._build()
        self._show_mode(self._cfg.auth_kind)

    def _build(self) -> None:
        from .. import __version__

        card = ctk.CTkFrame(self, fg_color=("gray92", "gray16"), corner_radius=12)
        card.place(relx=0.5, rely=0.5, anchor="center")
        outer = ctk.CTkFrame(card, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            outer, text=t("login.title", version=__version__),
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x", pady=(0, 8))

        if self._info:
            ctk.CTkLabel(
                outer, text=self._info, text_color=("#1d4ed8", "#93c5fd"),
                wraplength=380, justify="left",
            ).pack(fill="x", pady=(0, 8))

        # Error label is created now (so the step frames can pack `before` it)
        # but packed further down, after both step frames.
        self.error_var = ctk.StringVar(value="")
        self.error_label = ctk.CTkLabel(
            outer, textvariable=self.error_var,
            text_color="#991b1b", wraplength=380, justify="left",
        )

        # ---- Step 1: credentials ----
        self._creds_frame = ctk.CTkFrame(outer, fg_color="transparent")

        ctk.CTkLabel(
            self._creds_frame, text=t("login.intro"), wraplength=380, justify="left",
        ).pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(self._creds_frame, text=t("login.server_url_label"), anchor="w").pack(fill="x")
        self.server_url_var = ctk.StringVar(value=self._cfg.server_url)
        self._server_entry = ctk.CTkEntry(
            self._creds_frame, textvariable=self.server_url_var,
            placeholder_text=t("login.server_url_placeholder"),
        )
        self._server_entry.pack(fill="x", pady=(0, 12))
        self._entries.append(self._server_entry)

        # Password mode (email + password) and API-token mode share the row;
        # only one packs at a time via _show_mode.
        self._pw_frame = ctk.CTkFrame(self._creds_frame, fg_color="transparent")
        self._tok_frame = ctk.CTkFrame(self._creds_frame, fg_color="transparent")

        ctk.CTkLabel(self._pw_frame, text=t("login.email_label"), anchor="w").pack(fill="x")
        self.email_var = ctk.StringVar(value=self._cfg.last_email or "")
        self._email_entry = ctk.CTkEntry(self._pw_frame, textvariable=self.email_var)
        self._email_entry.pack(fill="x", pady=(0, 8))
        self._entries.append(self._email_entry)
        ctk.CTkLabel(self._pw_frame, text=t("login.password_label"), anchor="w").pack(fill="x")
        self.password_var = ctk.StringVar()
        password_entry = ctk.CTkEntry(self._pw_frame, textvariable=self.password_var, show="*")
        password_entry.pack(fill="x", pady=(0, 8))
        self._entries.append(password_entry)

        ctk.CTkLabel(self._tok_frame, text=t("login.api_token_label"), anchor="w").pack(fill="x")
        self.api_token_var = ctk.StringVar()
        api_token_entry = ctk.CTkEntry(
            self._tok_frame, textvariable=self.api_token_var, show="*",
            placeholder_text=t("login.api_token_placeholder"),
        )
        api_token_entry.pack(fill="x", pady=(0, 8))
        self._entries.append(api_token_entry)

        toggle_row = ctk.CTkFrame(self._creds_frame, fg_color="transparent")
        toggle_row.pack(fill="x", pady=(4, 8))
        self.toggle_label = ctk.CTkLabel(
            toggle_row, text=t("login.toggle_to_token"),
            text_color=_LINK_COLOR, cursor="hand2",
        )
        self.toggle_label.pack(side="left")
        self.toggle_label.bind("<Button-1>", self._on_toggle)

        self._creds_frame.pack(fill="x")

        # ---- Step 2: second factor (hidden until TOTP_REQUIRED) ----
        self._code_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._signing_as_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self._code_frame, textvariable=self._signing_as_var,
            anchor="w", text_color=_MUTED_COLOR,
        ).pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(self._code_frame, text=t("login.code_label"), anchor="w").pack(fill="x")
        self.code_var = ctk.StringVar()
        self._code_entry = ctk.CTkEntry(
            self._code_frame, textvariable=self.code_var,
            placeholder_text=t("login.code_placeholder"),
        )
        self._code_entry.pack(fill="x", pady=(0, 4))
        self._entries.append(self._code_entry)
        ctk.CTkLabel(
            self._code_frame, text=t("login.code_help"),
            wraplength=380, justify="left", text_color=_MUTED_COLOR,
        ).pack(fill="x", pady=(0, 8))
        back = ctk.CTkLabel(
            self._code_frame, text=t("login.back_to_login"),
            text_color=_LINK_COLOR, cursor="hand2", anchor="w",
        )
        back.pack(fill="x")
        back.bind("<Button-1>", lambda _e: self._back_to_creds())

        # ---- error + buttons + progress ----
        self.error_label.pack(fill="x", pady=(0, 8))
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x")
        self.signin_btn = ctk.CTkButton(btn_row, text=t("login.sign_in"), command=self._on_signin)
        self.signin_btn.pack(side="right")
        ctk.CTkButton(
            btn_row, text=t("login.quit"), command=self._on_cancel, fg_color="gray",
        ).pack(side="right", padx=(0, 8))

        self._progress = ctk.CTkProgressBar(outer, mode="indeterminate")
        # packed in _set_busy(True), pack_forgot when idle.

        # Enter submits (routes by step) / Escape goes back or quits. Bound on
        # the entries (no grab_set) so the bindings die with the overlay.
        for entry in self._entries:
            entry.bind("<Return>", lambda _e: self._on_signin())
            entry.bind("<Escape>", lambda _e: self._on_escape())

    # ---- show / hide -----------------------------------------------------

    def show(self) -> None:
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self.after_idle(self._focus_first)

    def hide(self) -> None:
        self.place_forget()

    def _focus_first(self) -> None:
        try:
            if self._step == "code":
                self._code_entry.focus_set()
            elif self._cfg.auth_kind != "api_token" and self.server_url_var.get():
                self._email_entry.focus_set()
            else:
                self._server_entry.focus_set()
        except Exception:
            pass

    def _on_escape(self) -> None:
        if self._step == "code":
            self._back_to_creds()
        else:
            self._on_cancel()

    # ---- mode toggle (creds step) ---------------------------------------

    def _show_mode(self, kind: str) -> None:
        self._pw_frame.pack_forget()
        self._tok_frame.pack_forget()
        if kind == "api_token":
            self._tok_frame.pack(fill="x", before=self.toggle_label.master)
            self.toggle_label.configure(text=t("login.toggle_to_password"))
            stored = get_secret("api_token", self.server_url_var.get().rstrip("/"))
            if stored:
                self.api_token_var.set(stored)
        else:
            self._pw_frame.pack(fill="x", before=self.toggle_label.master)
            self.toggle_label.configure(text=t("login.toggle_to_token"))

    def _on_toggle(self, _event=None) -> None:
        current_kind = self._cfg.auth_kind
        self._cfg.auth_kind = "api_token" if current_kind != "api_token" else "password"
        self._show_mode(self._cfg.auth_kind)

    # ---- step transitions ------------------------------------------------

    def _enter_code_step(self, email: str) -> None:
        self._step = "code"
        self._set_busy(False)
        self.error_var.set("")
        self.code_var.set("")
        self._creds_frame.pack_forget()
        self._signing_as_var.set(t("login.signing_in_as", email=email))
        self._code_frame.pack(fill="x", before=self.error_label)
        self.after_idle(lambda: self._code_entry.focus_set())

    def _back_to_creds(self) -> None:
        self._step = "creds"
        self._set_busy(False)
        self.error_var.set("")
        self.code_var.set("")
        self._code_frame.pack_forget()
        self._creds_frame.pack(fill="x", before=self.error_label)
        self.after_idle(self._focus_first)

    # ---- busy state ------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.signin_btn.configure(state="disabled", text=t("login.signing_in"))
            self._progress.pack(fill="x", pady=(8, 0))
            self._progress.start()
        else:
            self._stop_spinner()
            self.signin_btn.configure(state="normal", text=t("login.sign_in"))

    def _stop_spinner(self) -> None:
        try:
            self._progress.stop()
            self._progress.pack_forget()
        except Exception:
            pass

    # ---- sign-in ---------------------------------------------------------

    def _on_signin(self) -> None:
        self.error_var.set("")
        # Snapshot Tk vars on the main thread BEFORE spawning the worker —
        # reading StringVar.get() off-thread deadlocks Tk on Windows.
        server_raw = self.server_url_var.get().strip().rstrip("/")
        if not server_raw:
            self._show_error(t("login.err_server_required"))
            return
        try:
            server = normalize_server_url(server_raw)
        except ValueError as e:
            self._show_error(str(e))
            return

        # --- Step 2: second factor (TOTP or recovery, auto-routed) ---
        if self._step == "code":
            code = self.code_var.get().strip()
            if not code:
                self._show_error(t("login.err_code_required"))
                return
            email = self.email_var.get().strip()
            password = self.password_var.get()
            normalized = code.replace(" ", "")
            use_totp = bool(_TOTP_RE.match(normalized))
            self._set_busy(True)

            def _attempt():
                trace(f"_attempt code step (totp={use_totp})")
                api = ApiClient(server)
                if use_totp:
                    api_pkg.login(api, email=email, password=password, totp_code=normalized)
                else:
                    api_pkg.login_with_recovery(
                        api, email=email, password=password, recovery_code=code,
                    )
                me = api_pkg.me(api)
                return api, me, "password", server, email

            run_in_background(self._app_root, _attempt, on_done=self._done, on_failed=self._failed)
            return

        # --- Step 1: credentials ---
        kind = self._cfg.auth_kind
        if kind == "api_token":
            api_token = self.api_token_var.get().strip()
            if not api_token:
                self._show_error(t("login.err_api_token_required"))
                return
            self._set_busy(True)

            def _attempt():
                trace("_attempt api_token")
                api = ApiClient(server, api_token=api_token)
                me = api_pkg.me(api)
                set_secret("api_token", server, api_token)
                return api, me, "api_token", server, ""

            run_in_background(self._app_root, _attempt, on_done=self._done, on_failed=self._failed)
            return

        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not email:
            self._show_error(t("login.err_email_required"))
            return
        if not password:
            self._show_error(t("login.err_password_required"))
            return
        self._set_busy(True)

        def _attempt():
            trace("_attempt creds (password, no 2FA code yet)")
            api = ApiClient(server)
            # No totp_code: if 2FA is on the server returns TOTP_REQUIRED, which
            # _failed turns into the code step (the password isn't penalised).
            api_pkg.login(api, email=email, password=password, totp_code=None)
            me = api_pkg.me(api)
            return api, me, "password", server, email

        run_in_background(self._app_root, _attempt, on_done=self._done, on_failed=self._failed)

    def _done(self, result) -> None:
        trace("_done (on main thread)")
        self._stop_spinner()
        api, me, used_kind, server, email = result
        self._cfg.server_url = server
        self._cfg.auth_kind = used_kind
        if used_kind == "password":
            self._cfg.last_email = email
        try:
            save_config(self._cfg)
        except Exception as exc:
            trace(f"save_config FAILED: {exc!r}")
        # Hand off to the controller. On success it hides + destroys this
        # overlay, so don't touch self after. If it raises (MainWindow
        # construction failed) the overlay stays up and we restore the button.
        try:
            self._on_signed_in(api, me)
        except Exception as exc:
            trace(f"_on_signed_in RAISED: {exc!r}")
            import traceback
            traceback.print_exc()
            self._set_busy(False)
            self._show_error(t("login.err_open_main_window", detail=repr(exc)))

    def _failed(self, exc) -> None:
        trace(f"_failed: {type(exc).__name__}: {exc!r}")
        self._set_busy(False)
        # v0.4.6: write every caught sign-in failure to crash.log with a full
        # traceback — the surface message alone isn't enough to debug DLL-load
        # / SSL-import / OS-level failures.
        try:
            import platformdirs
            import traceback
            from datetime import datetime
            from pathlib import Path
            log_path = (
                Path(platformdirs.user_log_dir("fileHeron", appauthor=False)) / "crash.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} [signin] ---\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        except Exception:
            pass  # logging must never crash the UI thread

        if isinstance(exc, ApiError):
            if exc.code == "TOTP_REQUIRED":
                # Password was accepted; reveal the second-factor step.
                self._enter_code_step(self.email_var.get().strip())
                return
            if exc.code == "INVALID_TOTP":
                self._show_error(t("login.err_invalid_totp"))
                self.code_var.set("")
                return
            if exc.code == "INVALID_RECOVERY":
                self._show_error(t("login.err_invalid_recovery"))
                self.code_var.set("")
                return
            self._show_error(exc.message or t("login.err_signin_failed"))
            return
        # Network / TLS / DNS.
        self._show_error(t("login.err_unreachable", detail=str(exc)))

    def _show_error(self, msg: str) -> None:
        self.error_var.set(msg)
