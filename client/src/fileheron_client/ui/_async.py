"""Thread-safe UI update primitive for the CustomTkinter migration.

v0.4.0 / v0.4.3 attempted this with worker threads calling
``root.after(0, callback)`` directly. That broke sign-in on Windows:
``root.after()`` goes through ``tk.call`` which acquires Tcl's
interpreter lock, but the main thread sitting in ``wait_window()`` or
``mainloop()`` holds that lock — the worker either deadlocks waiting
for it or the scheduled callback never gets serviced.

v0.4.4 switches to the canonical Tk-threading pattern:

- Worker threads NEVER touch any Tk API. They put ``(callback, args)``
  tuples on a thread-safe ``queue.Queue``.
- The Tk main thread polls that queue every 50 ms via ``after()``.
  Pending callbacks run on the main thread inside the normal event
  loop, so widget mutations are legal.

``init_async(root)`` must be called once on the main thread after the
root is built; it kicks off the polling loop."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional

from ..api.client import SessionExpiredError

_log = logging.getLogger("fileheron_client.ui._async")

# Set once by AppController. When a worker raises SessionExpiredError we route
# it here (marshaled to the main thread) instead of the per-call on_failed, so
# every panel bounces back to login without special-casing it individually.
_session_expired_handler: Optional[Callable[[], None]] = None


def set_session_expired_handler(fn: Optional[Callable[[], None]]) -> None:
    global _session_expired_handler
    _session_expired_handler = fn


def _route_failure(
    exc: Exception, on_failed: Optional[Callable[[Exception], None]]
) -> None:
    """Marshal a worker-thread failure onto the main-thread queue. A dead
    session is intercepted and sent to the global handler; everything else
    goes to the call's own ``on_failed``."""
    if isinstance(exc, SessionExpiredError) and _session_expired_handler is not None:
        _enqueue(_session_expired_handler, ())
        return
    if on_failed is not None:
        _enqueue(on_failed, (exc,))

# Module-global queue. Workers push (callback, args) tuples; the main-
# thread poller in init_async drains them. Simpler than wiring a queue
# per call site, and there's only ever one Tk main loop.
_result_q: "queue.Queue[tuple[Callable[..., Any], tuple]]" = queue.Queue()

_POLL_INTERVAL_MS = 50


def init_async(root) -> None:
    """Wire the main-thread polling loop. Idempotent — calling more
    than once just re-schedules the next tick."""
    def _poll() -> None:
        try:
            while True:
                callback, args = _result_q.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    # Don't let one bad callback stall the whole queue;
                    # log + continue. report_callback_exception in
                    # __main__ will also capture this to crash.log via
                    # the standard Tk path.
                    _log.exception("async callback failed")
        except queue.Empty:
            pass
        try:
            root.after(_POLL_INTERVAL_MS, _poll)
        except RuntimeError:
            # Root was destroyed; stop polling.
            pass

    # Schedule first poll one tick out so init_async can be called
    # immediately after root construction, before any UI is built.
    root.after(_POLL_INTERVAL_MS, _poll)


def _enqueue(callback: Callable[..., Any], args: tuple) -> None:
    """Put a callback on the main-thread queue. Drop silently on full
    queue (never happens with the default unbounded queue, but the
    paranoia keeps this safe under future refactors)."""
    try:
        _result_q.put_nowait((callback, args))
    except queue.Full:
        _log.warning("async result queue full; dropping callback %s", callback)


def run_in_background(
    root,
    fn: Callable[[], Any],
    *,
    on_done: Optional[Callable[[Any], None]] = None,
    on_failed: Optional[Callable[[Exception], None]] = None,
) -> threading.Thread:
    """Run ``fn`` in a daemon thread. On completion, queue ``on_done``
    or ``on_failed`` for the next main-thread poll.

    ``root`` is accepted but unused in v0.4.4 (the queue is module-
    scoped) — kept in the signature so call sites don't need to change."""
    def _runner() -> None:
        try:
            result = fn()
        except Exception as exc:
            _route_failure(exc, on_failed)
            return
        if on_done is not None:
            _enqueue(on_done, (result,))

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
    """Run ``fn(_tick)`` in a daemon thread. Progress ticks marshal
    onto the main thread via the same queue. ``on_done`` /
    ``on_failed`` behave identically to ``run_in_background``."""
    def _tick(done: int, total: int) -> None:
        if on_progress is not None:
            _enqueue(on_progress, (done, total))

    def _runner() -> None:
        try:
            result = fn(_tick)
        except Exception as exc:
            _route_failure(exc, on_failed)
            return
        if on_done is not None:
            _enqueue(on_done, (result,))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t
