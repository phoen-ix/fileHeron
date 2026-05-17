"""Lifecycle breadcrumb writer (v0.4.12). Other modules call
``trace("event happened")`` to leave a timestamped breadcrumb in
``trace.log`` next to ``crash.log``. Lets us tell *where* the
process died when crash.log is empty (silent C-level crash, clean
exit, etc).

The writer is module-global and initialised by ``__main__.main()``.
Calls made before init are silent no-ops (so it's safe to import +
call from anywhere)."""
from __future__ import annotations

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
