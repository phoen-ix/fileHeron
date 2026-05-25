"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script.

v0.4.0 architecture: one ``ctk.CTk`` root is created up-front and
shared across the login phase + main window. We hide the root during
login (the login dialog is a separate ``CTkToplevel``) and reveal it
after a successful sign-in.

**Two-layer logging (v0.4.16):**

Layer 1 — **always on**. Crash reporting only.
- ``crash.log`` — uncaught Python exceptions (sys/threading/Tk
  excepthooks) AND native-level crashes (via ``faulthandler``).
  Cheap, silent in normal operation; the only post-mortem we have
  when the .exe (``console=False``) dies.

Layer 2 — **gated on ``ClientConfig.enable_diagnostic_logging``,
default OFF**. Verbose diagnostics added across v0.4.12 → v0.4.15
to debug the invisible-window bug:
- ``trace.log`` — explicit lifecycle breadcrumbs.
- ``app.log``   — ``logging`` output (``_log.exception`` etc.).
- Heartbeat polling root window state every 2s for 10s after
  mainloop entry.

The flag is also surfaced in the Settings dialog (CTkSwitch); takes
effect on next launch because the loggers are wired at startup.
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from fileheron_client._trace import init as init_trace, trace
from fileheron_client.config import load_config, save_config
from fileheron_client.i18n import set_locale
from fileheron_client.ui._async import init_async
from fileheron_client.ui.app import build_root
from fileheron_client.ui.login_window import LoginWindow
from fileheron_client.ui.main_window import MainWindow


def _log_dir() -> Path:
    import platformdirs
    d = Path(platformdirs.user_log_dir("fileHeron", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _install_crash_logging(log_path: Path) -> None:
    """Capture uncaught Python exceptions from every thread + Tk
    callback dispatch into ``crash.log``. Always installed — this is
    the post-mortem safety net, not verbose diagnostics."""

    def _write(prefix: str, exc_type, exc, tb) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} [{prefix}] ---\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass

    def _excepthook(exc_type, exc, tb):
        _write("main", exc_type, exc, tb)
        traceback.print_exception(exc_type, exc, tb)

    def _thread_excepthook(args):
        _write(
            f"thread:{args.thread.name}",
            args.exc_type, args.exc_value, args.exc_traceback,
        )
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def _install_faulthandler(log_path: Path) -> None:
    """Native-level crash dumper. Catches SIGSEGV / SIGABRT / SIGFPE
    / SIGBUS / SIGILL and writes a C-stack dump to crash.log. The
    only way to learn anything about a tkinter/Tcl native crash that
    bypasses Python's exception machinery. Always installed."""
    try:
        fh_file = log_path.open("a", encoding="utf-8")
        fh_file.write(f"\n--- {datetime.now().isoformat()} [faulthandler armed] ---\n")
        fh_file.flush()
        faulthandler.enable(file=fh_file, all_threads=True)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    log_dir = _log_dir()
    crash_log = log_dir / "crash.log"

    # Layer 1 (always on): crash reporting.
    _install_crash_logging(crash_log)
    _install_faulthandler(crash_log)

    # Read config BEFORE wiring verbose logging so the flag can gate it.
    # If load_config itself raises (corrupt JSON), the always-on crash
    # hooks above capture it.
    cfg = load_config()

    # v0.8.0: apply the cached locale (from the previous sign-in) so the
    # login window already renders in the user's language. Updated below
    # in _on_signin once we have the server-side users.locale.
    set_locale(cfg.locale or "en")

    # Layer 2 (gated on cfg.enable_diagnostic_logging, default OFF):
    # trace.log breadcrumbs + app.log + heartbeat polling.
    diagnostics_on = bool(cfg.enable_diagnostic_logging)
    if diagnostics_on:
        init_trace(log_dir / "trace.log")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=[
                logging.FileHandler(log_dir / "app.log", encoding="utf-8")
            ],
        )
        logging.info("fileHeron client starting; crash log: %s", crash_log)
        trace("=== process start ===")

        @atexit.register
        def _on_exit() -> None:
            trace("=== process exit (clean) ===")
    else:
        # Defensive: make sure no logger ever bubbles to stderr (which
        # is /dev/null under console=False anyway) by attaching a
        # NullHandler at the root and silencing everything below
        # CRITICAL. trace() is already a no-op when init_trace was
        # never called.
        logging.basicConfig(
            level=logging.CRITICAL, handlers=[logging.NullHandler()]
        )

    # trace() calls past this point are no-ops when diagnostics are off.
    trace("building root")
    root = build_root()

    def _tk_report(exc_type, exc, tb):
        # Tk callback exception handler — always writes crash.log
        # regardless of the diagnostics flag.
        try:
            with crash_log.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} [tk] ---\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        traceback.print_exception(exc_type, exc, tb)
    root.report_callback_exception = _tk_report

    trace("initialising async poller")
    init_async(root)

    root.withdraw()

    main_window: MainWindow | None = None

    def _on_signin(api, me) -> None:
        # v0.4.13: only CONSTRUCT MainWindow here (widgets built into
        # the still-withdrawn root). Actual show() happens AFTER login
        # is destroyed — see below. The previous order (show before
        # destroy) left the root in a stuck-withdrawn state on Windows
        # — login's transient teardown swallowed the deiconify.
        nonlocal main_window
        trace(f"_on_signin called (role={me.role}, locale={me.locale!r})")
        # v0.8.0: apply + cache the server-side locale so the main
        # window renders correctly and subsequent launches start in
        # the right language. Failure to persist isn't fatal — the
        # in-memory set_locale call has already taken effect.
        try:
            set_locale(me.locale or "en")
            if cfg.locale != (me.locale or ""):
                cfg.locale = me.locale or ""
                try:
                    save_config(cfg)
                except Exception as exc:
                    trace(f"save_config (locale) failed: {exc!r}")
        except Exception as exc:
            trace(f"locale wiring failed (non-fatal): {exc!r}")
        try:
            main_window = MainWindow(root, api, me)
            trace("MainWindow constructed (will show after login destroyed)")
        except BaseException as exc:
            trace(f"_on_signin RAISED: {type(exc).__name__}: {exc!r}")
            raise

    trace("opening LoginWindow")
    login = LoginWindow(root, cfg, on_signed_in=_on_signin)
    login.show_modal()
    trace(f"LoginWindow.show_modal returned; main_window set? {main_window is not None}")

    if main_window is None:
        trace("no main_window — destroying root + exiting")
        root.destroy()
        return 0

    trace("calling MainWindow.show() (login already destroyed)")
    main_window.show()
    trace(f"after show(): root.state()={root.state()!r} viewable={bool(root.winfo_viewable())}")

    # Heartbeat (diagnostic only): every 2s for the first 10s, log
    # root visibility state so we can diagnose "window never appears"
    # complaints. Skipped entirely when diagnostics are off.
    if diagnostics_on:
        def _heartbeat(tick: int = 0) -> None:
            try:
                trace(
                    f"heartbeat#{tick} state={root.state()!r} "
                    f"viewable={bool(root.winfo_viewable())} "
                    f"geom={root.winfo_geometry()!r}"
                )
            except Exception as exc:
                trace(f"heartbeat#{tick} FAILED: {exc!r}")
                return
            if tick < 5:
                root.after(2000, lambda: _heartbeat(tick + 1))
        root.after(500, _heartbeat)

    trace("entering root.mainloop")
    try:
        root.mainloop()
    except BaseException as exc:
        trace(f"mainloop RAISED: {type(exc).__name__}: {exc!r}")
        raise
    trace("mainloop returned cleanly")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
