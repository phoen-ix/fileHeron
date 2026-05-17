"""Thread-safe UI update primitive for the CustomTkinter migration.

Tkinter is single-threaded for UI mutations; touching a widget from a
worker thread is undefined behaviour (sometimes crashes, sometimes
silently corrupts). Qt's signals/slots auto-marshalled across threads;
in tkinter we have to do that ourselves via ``widget.after(0, ...)``,
which schedules a callable to run on the next idle tick of the main
event loop.

Two helpers cover the patterns the app needs:

- ``run_in_background(root, fn, on_done, on_failed)`` — fire-and-forget
  for one-shot calls (login, list_shares, revoke_share, etc.). The
  callbacks run on the Tk main thread.
- ``run_with_progress(root, fn, on_progress, on_done, on_failed)`` —
  for calls that take an ``on_progress(done, total)`` callback
  themselves (download_file, upload_direct, TUS). ``fn`` is invoked
  with a thread-side progress callback that internally marshals each
  tick onto the Tk main loop, so the callsite never sees the
  threading boundary.

Both return the spawned ``threading.Thread`` for callers who want to
``.join()`` (rare — these are daemon threads).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional


def run_in_background(
    root,
    fn: Callable[[], Any],
    *,
    on_done: Optional[Callable[[Any], None]] = None,
    on_failed: Optional[Callable[[Exception], None]] = None,
) -> threading.Thread:
    """Run ``fn`` in a daemon thread. On completion, schedule
    ``on_done(result)`` or ``on_failed(exc)`` on the Tk main loop."""
    def _runner() -> None:
        try:
            result = fn()
        except Exception as exc:  # network / unexpected — surface to UI
            if on_failed is not None:
                try:
                    root.after(0, on_failed, exc)
                except RuntimeError:
                    # root was destroyed while the worker was running;
                    # drop the result silently rather than crashing.
                    pass
            return
        if on_done is not None:
            try:
                root.after(0, on_done, result)
            except RuntimeError:
                pass

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t


def run_with_progress(
    root,
    fn: Callable[[Callable[[int, int], None]], Any],
    *,
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_done: Optional[Callable[[Any], None]] = None,
    on_failed: Optional[Callable[[Exception], None]] = None,
) -> threading.Thread:
    """Run ``fn(_tick)`` in a daemon thread. The supplied ``_tick``
    function marshals each ``(done, total)`` update onto the Tk main
    loop before invoking ``on_progress``. ``on_done`` / ``on_failed``
    behave identically to ``run_in_background``."""
    def _tick(done: int, total: int) -> None:
        if on_progress is None:
            return
        try:
            root.after(0, on_progress, done, total)
        except RuntimeError:
            pass

    def _runner() -> None:
        try:
            result = fn(_tick)
        except Exception as exc:
            if on_failed is not None:
                try:
                    root.after(0, on_failed, exc)
                except RuntimeError:
                    pass
            return
        if on_done is not None:
            try:
                root.after(0, on_done, result)
            except RuntimeError:
                pass

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t
