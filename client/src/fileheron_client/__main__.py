"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script.

v0.4.0 architecture: one ``ctk.CTk`` root is created up-front and
shared across the login phase + main window. We hide the root during
login (the login dialog is a separate ``CTkToplevel``) and reveal it
after a successful sign-in.

v0.4.3 added crash logging — the .exe ships with ``console=False`` so
stderr goes nowhere by default; without the excepthooks installed
below, a crash inside an event-handler is invisible to the user."""
from __future__ import annotations

import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

# Absolute imports — when PyInstaller runs this file as the entry
# script it loses package context, so `from .config import ...` would
# raise `ImportError: attempted relative import with no known parent
# package`. Absolute imports work both for that and for the canonical
# `python -m fileheron_client` invocation.
from fileheron_client.config import load_config
from fileheron_client.ui._async import init_async
from fileheron_client.ui.app import build_root
from fileheron_client.ui.login_window import LoginWindow
from fileheron_client.ui.main_window import MainWindow


def _crash_log_path() -> Path:
    """``%LOCALAPPDATA%\\fileHeron\\logs\\crash.log`` on Windows;
    ``~/.local/state/fileHeron/logs/crash.log`` on Linux."""
    import platformdirs
    return Path(platformdirs.user_log_dir("fileHeron", appauthor=False)) / "crash.log"


def _install_crash_logging() -> Path:
    """v0.4.3 — capture uncaught exceptions from the main thread + worker
    threads into a single file. Tk's own event-handler exceptions are
    hooked separately in main() via root.report_callback_exception
    (it's a Tk-instance attribute, not a sys-level hook)."""
    log_path = _crash_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(prefix: str, exc_type, exc, tb) -> None:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat()} [{prefix}] ---\n")
            traceback.print_exception(exc_type, exc, tb, file=f)

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


def main(argv: list[str] | None = None) -> int:
    log_path = _install_crash_logging()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logging.info("fileHeron client starting; crash log: %s", log_path)

    root = build_root()

    # Catch exceptions raised by Tk event-handler callbacks (button
    # commands, after() callbacks). These don't go through sys.excepthook
    # because Tk catches them in its own dispatcher.
    def _tk_report(exc_type, exc, tb):
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat()} [tk] ---\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
        traceback.print_exception(exc_type, exc, tb)
    root.report_callback_exception = _tk_report

    # v0.4.4: kick the main-thread async-result poller. Worker
    # threads push (callback, args) onto a queue.Queue; the poller
    # drains it from the main thread every 50 ms. Without this, the
    # sign-in worker's _done callback never fires (Tk's threading
    # rules forbid calling .after() from a worker thread).
    init_async(root)

    # Hide the root during the login phase. The login dialog is a
    # Toplevel on top of (and modal to) the hidden root.
    root.withdraw()

    cfg = load_config()
    main_window: MainWindow | None = None

    def _on_signin(api, me) -> None:
        nonlocal main_window
        main_window = MainWindow(root, api, me)
        main_window.show()

    login = LoginWindow(root, cfg, on_signed_in=_on_signin)
    login.show_modal()

    # If login was cancelled (no MainWindow attached), exit cleanly.
    if main_window is None:
        root.destroy()
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
