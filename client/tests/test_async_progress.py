"""run_with_progress coalesces progress callbacks (download/upload UI freeze fix).

A fast producer used to enqueue one progress callback per chunk; the main-thread
poller drains until empty, so it never returned to the event loop and the UI
froze until the transfer finished. run_with_progress now keeps only the latest
(done, total) with at most one flush queued. With no poller running in the test,
the pending flag never clears, so exactly ONE progress flush is enqueued - and it
carries the LAST value.
"""
from __future__ import annotations

import queue

from fileheron_client.ui import _async


def _drain():
    items = []
    while True:
        try:
            items.append(_async._result_q.get_nowait())
        except queue.Empty:
            return items


def test_progress_is_coalesced_to_latest():
    _drain()  # clear any residue
    progress: list[tuple[int, int]] = []
    done_results: list = []

    def fn(tick):
        for i in range(1000):
            tick(i, 1000)
        return "ok"

    t = _async.run_with_progress(
        None, fn,
        on_progress=lambda d, total: progress.append((d, total)),
        on_done=lambda r: done_results.append(r),
    )
    t.join(timeout=5)
    assert not t.is_alive()

    items = _drain()
    # Run the queued callbacks (as the main-thread poller would).
    for cb, args in items:
        cb(*args)

    # Exactly one progress flush was queued (coalesced), not 1000.
    assert len(progress) == 1, f"expected 1 coalesced progress, got {len(progress)}"
    # ...and it carries the final value.
    assert progress[0] == (999, 1000)
    # on_done still fired.
    assert done_results == ["ok"]


def test_no_progress_callback_when_none():
    _drain()

    def fn(tick):
        tick(1, 2)  # no-op since on_progress is None
        return 42

    out: list = []
    t = _async.run_with_progress(None, fn, on_done=lambda r: out.append(r))
    t.join(timeout=5)
    items = _drain()
    for cb, args in items:
        cb(*args)
    assert out == [42]


def test_init_async_is_idempotent_per_root(monkeypatch):
    """Each call scheduled another poll loop, so a second call doubled the
    polling for the life of the app (audit 2026-11-08)."""
    from fileheron_client.ui import _async

    monkeypatch.setattr(_async, "_polling_root", None)

    class _Root:
        def __init__(self):
            self.scheduled = 0

        def after(self, _ms, _fn):
            self.scheduled += 1

    root = _Root()
    _async.init_async(root)
    _async.init_async(root)
    assert root.scheduled == 1

    other = _Root()
    _async.init_async(other)
    assert other.scheduled == 1, "a NEW root still gets its loop"
