"""AppController - owns the single root and swaps between the login overlay
and the main window inside it (v0.9.1).

Before v0.9.1 the entry point ran a modal ``LoginWindow`` toplevel, then on
success constructed + showed ``MainWindow`` and entered the mainloop; logout
called ``root.destroy()`` (the app quit) and an expired session left a dead
screen. The overlay refactor turns those procedural steps into a long-lived
coordinator: it outlives individual ``MainWindow`` instances (rebuilt each
login), so it's the stable place to hold "which screen are we on" and to
broker the transitions:

- sign-in  → build MainWindow, remove the overlay
- sign-out → tear down MainWindow, re-show a fresh overlay (no app exit)
- session-expiry → same, with an informational banner
"""
from __future__ import annotations

from typing import Optional

from .._trace import trace
from ..api import ApiClient
from ..config import ClientConfig, clear_secret, save_config
from ..i18n import set_locale, t
from ..models import MeResponse
from ._async import set_session_expired_handler
from .app import reassert_visible
from .login_window import LoginOverlay
from .main_window import MainWindow


class AppController:
    def __init__(self, root, cfg: ClientConfig) -> None:
        self._root = root
        self._cfg = cfg
        self._overlay: Optional[LoginOverlay] = None
        self._main: Optional[MainWindow] = None
        self._api: Optional[ApiClient] = None

    def start(self) -> None:
        """Show the root (visible from the start now - no withdraw) with the
        login overlay placed on top, and enter the single mainloop (run by
        the caller)."""
        set_session_expired_handler(self.session_expired)
        self._root.protocol("WM_DELETE_WINDOW", self._on_root_close)
        self._root.deiconify()
        self._root.lift()
        # Safety net for CTk's Windows titlebar-withdraw routine on first show.
        reassert_visible(self._root, 60)
        # Initial show only: auto sign-in if a stored API token is present.
        self._show_overlay(auto_login=True)

    def _on_root_close(self) -> None:
        """Window-manager close (the X button) handler. On a normal close
        while signed in with a PASSWORD session, revoke that session
        server-side (best-effort, short timeout) so it doesn't linger until
        the cleanup cron. API-token logins have no session to revoke - and we
        must NOT touch the persistent token (it's reused on the next launch).
        A crash skips this entirely; the cron reaps those."""
        api = self._api
        if api is not None and self._main is not None and api.api_token is None:
            trace("normal close - revoking password session")
            try:
                from ..api import auth as auth_pkg

                auth_pkg.logout(api, timeout=3.0)
            except Exception as exc:
                trace(f"logout-on-close failed (non-fatal): {exc!r}")
        try:
            self._root.destroy()
        except Exception:
            pass

    # ---- screen transitions ---------------------------------------------

    def _show_overlay(
        self, *, info: Optional[str] = None, auto_login: bool = False
    ) -> None:
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
        self._overlay = LoginOverlay(
            self._root, self._cfg,
            on_signed_in=self._on_signed_in,
            on_cancel=self._root.destroy,  # nothing behind the overlay → quit
            info=info,
            auto_login=auto_login,
        )
        self._overlay.show()
        try:
            self._overlay.focus_set()
        except Exception:
            pass

    def _on_signed_in(self, api: ApiClient, me: MeResponse) -> None:
        trace(f"_on_signed_in (role={me.role}, locale={me.locale!r})")
        # v0.8.0: apply + cache the server-side locale so the main window
        # renders correctly and subsequent launches start in the right
        # language. Failure to persist isn't fatal - the in-memory
        # set_locale call has already taken effect.
        try:
            set_locale(me.locale or "en")
            if self._cfg.locale != (me.locale or ""):
                self._cfg.locale = me.locale or ""
                try:
                    save_config(self._cfg)
                except Exception as exc:
                    trace(f"save_config (locale) failed: {exc!r}")
        except Exception as exc:
            trace(f"locale wiring failed (non-fatal): {exc!r}")

        # Adopt the instance's timezone for rendering AND for interpreting the
        # expiry the operator types. Without it the client used the machine's
        # local zone for both while the SPA used `site.timezone`, so a
        # travelling employee set an expiry six hours from the one the
        # recipient saw, with no zone label anywhere to reveal it (audit #2).
        try:
            from ..api.site import public_config
            from ..formatters import set_display_timezone

            set_display_timezone(public_config(api).get("site_timezone"))
        except Exception as exc:
            trace(f"site timezone wiring failed (non-fatal): {exc!r}")

        # Build the main UI behind the overlay, THEN remove the overlay so the
        # root is never momentarily empty. If MainWindow construction raises,
        # the exception propagates back into LoginOverlay._done, which keeps
        # the overlay up and shows the error - so don't swallow it here.
        self._api = api
        self._main = MainWindow(self._root, api, me, on_signed_out=self.logout)
        if self._overlay is not None:
            self._overlay.hide()
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None
        self._main.post_show()

    def logout(self) -> None:
        """Wired as MainWindow/SettingsDialog's ``on_signed_out``. The dialog
        already called ``api.logout()`` + ``clear_secret()``; we just tear the
        main UI down and return to a fresh login overlay (the app no longer
        quits on logout)."""
        trace("logout - returning to login overlay")
        self._teardown_main()
        self._show_overlay()

    def session_expired(self) -> None:
        """Invoked (on the main thread) when a worker hit a 401 that couldn't
        be refreshed. Idempotent - concurrent 401s coalesce because the first
        call nulls ``self._main`` synchronously."""
        if self._main is None:
            return
        trace("session expired - bouncing to login overlay")
        if self._api is not None:
            try:
                clear_secret("refresh", self._api.server_url)
            except Exception:
                pass
        self._teardown_main()
        self._show_overlay(info=t("login.session_expired"))

    def _teardown_main(self) -> None:
        if self._main is not None:
            try:
                self._main.teardown()
            except Exception:
                pass
            self._main = None
        if self._api is not None:
            try:
                self._api.close()  # release the httpx pool + cookie jar
            except Exception:
                pass
            self._api = None
