"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script.

v0.4.0 architecture: one ``ctk.CTk`` root is created up-front and
shared across the login phase + main window. We hide the root during
login (the login dialog is a separate ``CTkToplevel``) and reveal it
after a successful sign-in.

v0.4.12 diagnostic hardening: the .exe ships with ``console=False``
so stderr goes nowhere by default. We now layer THREE log surfaces:

1. ``crash.log``  — uncaught Python exceptions (sys/threading/Tk
   excepthooks) AND native-level crashes (via ``faulthandler``).
2. ``app.log``    — ``logging`` output (the ``_log.exception(...)``
   calls inside ``_async.py`` etc — previously lost to nowhere).
3. ``trace.log``  — explicit breadcrumbs at every major lifecycle
   step. Lets us tell "process exited cleanly without doing X" from
   "process crashed during X" when crash.log is empty.
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
from fileheron_client.config import load_config
from fileheron_client.ui._async import init_async
from fileheron_client.ui.app import build_root
from fileheron_client.ui.login_window import LoginWindow
from fileheron_client.ui.main_window import MainWindow


def _log_dir() -> Path:
    import platformdirs
    d = Path(platformdirs.user_log_dir("fileHeron", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _install_crash_logging() -> Path:
    """Capture uncaught Python exceptions from every thread + Tk
    callback dispatch. Returns the crash.log path."""
    log_path = _log_dir() / "crash.log"

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
    return log_path


def _install_faulthandler(log_path: Path) -> None:
    """Native-level crash dumper. Catches SIGSEGV / SIGABRT / SIGFPE
    / SIGBUS / SIGILL and writes a C-stack dump to crash.log. The
    only way to learn anything about a tkinter/Tcl native crash that
    bypasses Python's exception machinery."""
    try:
        fh_file = log_path.open("a", encoding="utf-8")
        fh_file.write(f"\n--- {datetime.now().isoformat()} [faulthandler armed] ---\n")
        fh_file.flush()
        faulthandler.enable(file=fh_file, all_threads=True)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    log_dir = _log_dir()
    init_trace(log_dir / "trace.log")
    trace("=== process start ===")

    crash_log = _install_crash_logging()
    _install_faulthandler(crash_log)

    # Route logging output to app.log instead of stderr (which is
    # /dev/null in a console=False PyInstaller build).
    app_log = log_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(app_log, encoding="utf-8")],
    )
    logging.info("fileHeron client starting; crash log: %s", crash_log)

    @atexit.register
    def _on_exit() -> None:
        trace("=== process exit (clean) ===")

    trace("building root")
    root = build_root()

    def _tk_report(exc_type, exc, tb):
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

    cfg = load_config()
    main_window: MainWindow | None = None

    def _on_signin(api, me) -> None:
        nonlocal main_window
        trace(f"_on_signin called (role={me.role})")
        try:
            main_window = MainWindow(root, api, me)
            trace("MainWindow constructed")
            main_window.show()
            trace("MainWindow.show() returned")
        except BaseException as exc:
            trace(f"_on_signin RAISED: {type(exc).__name__}: {exc!r}")
            # Re-raise so LoginWindow._done can show the error in the dialog.
            raise

    trace("opening LoginWindow")
    login = LoginWindow(root, cfg, on_signed_in=_on_signin)
    login.show_modal()
    trace(f"LoginWindow.show_modal returned; main_window set? {main_window is not None}")

    if main_window is None:
        trace("no main_window — destroying root + exiting")
        root.destroy()
        return 0

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
