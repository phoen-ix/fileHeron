"""Entry point: ``python -m fileheron_client`` or the installed
``fileheron-client`` console script.

v0.9.1 architecture: one ``ctk.CTk`` root is created up-front and shown
immediately. ``AppController`` places a ``LoginOverlay`` (a CTkFrame) on top
of it and swaps overlay ⇄ main window inside that single root - one mainloop,
no separate login toplevel, no ``wait_window``. (Pre-v0.9.1 the root was
hidden during a modal login toplevel and revealed after sign-in.)

**Two-layer logging (v0.4.16):**

Layer 1 - **always on**. Crash reporting only.
- ``crash.log`` - uncaught Python exceptions (sys/threading/Tk
  excepthooks) AND native-level crashes (via ``faulthandler``).
  Cheap, silent in normal operation; the only post-mortem we have
  when the .exe (``console=False``) dies.

Layer 2 - **gated on ``ClientConfig.enable_diagnostic_logging``,
default OFF**. Verbose diagnostics added across v0.4.12 → v0.4.15
to debug the invisible-window bug:
- ``trace.log`` - explicit lifecycle breadcrumbs.
- ``app.log``   - ``logging`` output (``_log.exception`` etc.).
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

from fileheron_client._trace import init as init_trace
from fileheron_client._trace import report, trace
from fileheron_client.config import load_config
from fileheron_client.i18n import set_locale
from fileheron_client.ui._async import init_async
from fileheron_client.ui.app import build_root
from fileheron_client.ui.controller import AppController


def _log_dir() -> Path:
    from fileheron_client.config import log_dir
    return log_dir()


def _install_crash_logging(log_path: Path) -> None:
    """Capture uncaught Python exceptions from every thread + Tk
    callback dispatch into ``crash.log``. Always installed - this is
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


def _selfcheck() -> int:
    """Headless bundle smoke test (finding C11). Verifies the data files
    that pyinstaller.spec's collect_all() must bundle are actually present
    in the frozen image, so a packaging regression FAILS the release
    instead of shipping a .exe that crashes on a user's machine.

    Deliberately does NOT construct a Tk root - creating a GUI window can
    hang on a session-less CI runner (the process never exits under
    `Start-Process -Wait`). Importing the packages + checking their bundled
    data dirs on disk is enough to catch a missing-collect regression and
    always terminates quickly."""
    from pathlib import Path

    import customtkinter
    import tkinterdnd2

    problems: list[str] = []

    ctk_assets = Path(customtkinter.__file__).resolve().parent / "assets"
    if not ctk_assets.is_dir():
        problems.append(f"customtkinter assets missing: {ctk_assets}")

    # tzdata: the IANA time-zone database. Windows ships none of its own, so
    # without this bundled, `ZoneInfo` raises and the client silently falls back
    # to the machine's local zone - which is exactly what the site-timezone
    # support exists to stop doing, on the only platform this .exe runs on. That
    # shipped as client-v1.3.0 and cost a version number.
    #
    # Checked BEHAVIOURALLY, by constructing a real non-UTC zone, rather than by
    # looking for a directory: what matters is that `zoneinfo` can resolve one,
    # and a data-file layout check would pass while resolution failed. The probe
    # itself lives in formatters so the test suite can execute it - see there.
    from fileheron_client.formatters import timezone_database_problem

    tz_problem = timezone_database_problem()
    if tz_problem:
        problems.append(f"tzdata: {tz_problem}")

    # tkinterdnd2 ships its native `tkdnd` Tcl library under the package;
    # without it, drag-drop crashes at startup in the frozen .exe.
    dnd_root = Path(tkinterdnd2.__file__).resolve().parent
    if not (dnd_root / "tkdnd").is_dir() and not any(dnd_root.glob("tkdnd*")):
        problems.append(f"tkinterdnd2 tkdnd lib missing under: {dnd_root}")

    # The spec's OWN data trees, not just the third-party collect_all() ones.
    # These are plain (source, dest) entries, which is exactly why they were
    # overlooked: nothing about them looks fragile until a path in the spec
    # goes stale and the .exe ships with no translations - every label rendered
    # as its raw i18n key - or no icon.
    bundle = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).resolve().parent.parent)
    locales = bundle / "fileheron_client" / "locales"
    if not (locales / "en.json").is_file():
        problems.append(f"locale files missing: {locales}")
    assets = bundle / "assets"
    if getattr(sys, "frozen", False) and not assets.is_dir():
        problems.append(f"assets missing: {assets}")

    # NOTES, not problems: these describe the MACHINE, not the bundle, so they
    # are reported and do not affect the exit code. The release job runs this
    # against the freshly built .exe - failing it because a CI runner has no
    # credential vault would be precisely the kind of gate that fails for a
    # reason unrelated to what it is gating.
    from fileheron_client.config import keyring_problem

    keyring_issue = keyring_problem()
    if keyring_issue:
        # The first question support asks when a user reports "it forgets my
        # token every launch", and otherwise invisible: the failure is logged
        # to a logger that is silenced unless diagnostics are switched on.
        _report(f"selfcheck NOTE: keyring: {keyring_issue}")

    if problems:
        for p in problems:
            _report(f"selfcheck FAIL: {p}")
        return 1
    _report("selfcheck OK")
    return 0


def _report(line: str) -> None:
    """Self-check output, via the console-less-safe sink in _trace."""
    report(line, _log_dir() / "crash.log")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    log_dir = _log_dir()
    crash_log = log_dir / "crash.log"

    # Layer 1 (always on): crash reporting.
    _install_crash_logging(crash_log)
    _install_faulthandler(crash_log)

    # Bundle self-check short-circuits before any config/UI/login work.
    # Crash hooks are already installed so a failure lands in crash.log AND
    # exits non-zero for CI to catch.
    if "--selfcheck" in args:
        return _selfcheck()

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
        # Tk callback exception handler - always writes crash.log
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

    # Any download left "active" by a previous session (crash / force-quit) is
    # promoted to "interrupted" so the share view offers a Resume button.
    try:
        from fileheron_client import downloads_registry
        downloads_registry.reconcile_on_startup()
    except Exception:
        pass

    # The root is visible from the start; AppController places the login
    # overlay on top of it and owns every screen transition from here on
    # (sign-in → main, sign-out → overlay, session-expiry → overlay).
    trace("starting AppController (login overlay on the root)")
    AppController(root, cfg).start()

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
