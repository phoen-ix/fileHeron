"""Lifecycle breadcrumb writer (v0.4.12). Other modules call
``trace("event happened")`` to leave a timestamped breadcrumb in
``trace.log`` next to ``crash.log``. Lets us tell *where* the
process died when crash.log is empty (silent C-level crash, clean
exit, etc).

The writer is module-global and initialised by ``__main__.main()``.
Calls made before init are silent no-ops (so it's safe to import +
call from anywhere)."""
from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

_TRACE_PATH: Path | None = None


def init(path: Path) -> None:
    global _TRACE_PATH
    _TRACE_PATH = path


def trace(msg: str) -> None:
    if _TRACE_PATH is None:
        return
    try:
        with _TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [{threading.current_thread().name}] {msg}\n")
    except Exception:
        pass


def report(line: str, fallback_log: Path) -> None:
    """Emit a line somewhere a human can actually read it.

    ``console=False`` means the frozen .exe has no console of its own, so
    ``sys.stderr`` is ``None`` unless the caller supplied a handle - and
    ``sys.stderr.write`` on ``None`` raises, turning a diagnostic into the
    crash it exists to report. The release job's ``--selfcheck`` step redirects
    both handles, so it would have seen a non-zero exit with two empty log
    files and blamed the bundle.

    Lives here rather than in ``__main__`` so the test suite can execute it:
    importing ``__main__`` pulls in the GUI stack, which headless CI has no Tk
    for. That is the same reason the tzdata probe sits in ``formatters``.
    """
    for stream in (sys.stderr, sys.stdout):
        if stream is not None:
            try:
                stream.write(line + "\n")
                stream.flush()
                return
            except Exception:
                pass
    try:
        with fallback_log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
