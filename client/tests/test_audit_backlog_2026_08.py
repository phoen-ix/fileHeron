"""Audit-backlog closure, client side (P15, P16).

Both defects survived because the surrounding tests exercised the happy path
only: a download that succeeds never reaches the shutdown code, and a sign-in
that stores its token never reaches the warning.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import threading
import time

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, DownloadCancelled, DownloadPaused
from fileheron_client.api import download_checkpoint as ck
from fileheron_client.api import download_resumable as dr

SERVER = "https://files.example.com"
DATA = bytes((i % 251) for i in range(50))
ETAG = '"abc-50"'


def _api() -> ApiClient:
    return ApiClient(SERVER, api_token="fh_xx_yy")


# --- P16: the last worker-thread .after() in the client ----------------------


SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "fileheron_client"


def test_no_tk_call_survives_in_the_login_overlay():
    """Structural, so it runs on the Linux leg too - which has no Tk at all,
    and is therefore the leg where an import-time check proves nothing.

    _warn_token_not_stored's docstring claimed it marshalled back to the Tk
    thread; its body called ``self._app_root.after()``, the one thing _async.py
    exists to forbid. ``after()`` goes through ``tk.call``, which takes Tcl's
    interpreter lock - and it fires during sign-in, while the main thread is
    parked in ``wait_window()`` holding exactly that lock. That is the
    v0.4.0/v0.4.3 deadlock verbatim, and it was the last worker-thread
    ``after()`` left in the client."""
    tree = ast.parse((SRC / "ui" / "login_window.py").read_text(encoding="utf-8"))
    offenders = [
        f"line {n.lineno}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "after"
    ]
    assert not offenders, (
        "the login overlay reaches Tk directly again; everything here runs on "
        f"or off the sign-in worker thread and must go through _enqueue: {offenders}"
    )


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="needs a real Tk; the Linux CI image has none (that is the whole point)",
)
def test_the_token_warning_marshals_onto_the_main_thread_queue():
    """The behavioural half, on the leg that can import the UI.

    The stand-in deliberately has NO ``_app_root``, so a regression to any Tk
    call raises AttributeError here rather than deadlocking on a user's machine
    where nobody is looking."""
    from fileheron_client.ui import _async
    from fileheron_client.ui.login_window import LoginOverlay

    class _NoTk:
        def __init__(self) -> None:
            self.shown: list[str] = []

        def _show_error(self, msg: str) -> None:
            self.shown.append(msg)

    while not _async._result_q.empty():  # other tests share the module queue
        _async._result_q.get_nowait()

    overlay = _NoTk()
    LoginOverlay._warn_token_not_stored(overlay)

    # Nothing ran inline: the worker thread must not touch a StringVar.
    assert overlay.shown == []

    callback, args = _async._result_q.get_nowait()
    callback(*args)
    assert len(overlay.shown) == 1, "the warning never reached the Tk thread"
    assert overlay.shown[0], "the warning arrived empty"


# --- P15: the executor drained before the stop signal was ever set -----------


@respx.mock
def test_cancel_drops_the_queued_segments_instead_of_running_them(
    tmp_path, monkeypatch
):
    """shutdown(wait=True) without cancel_futures lets every QUEUED segment be
    scheduled and run. They exit at their own cancel check, so they issue no
    request - which is why counting requests proves nothing here, and why the
    first version of this test passed against the unfixed code. Count the CALLS.

    Two connections and five segments (the segmented path needs n > 1): at most
    two can be running when the pool is shut down, so the other three are still
    queued. Under the old code all five run."""
    monkeypatch.setattr(dr, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(dr, "SEGMENT_SIZE", 10)

    cancel = threading.Event()
    calls: list[int] = []

    def _fake_fetch(api, url, headers, part, start, end, bump, cancel_ev, pause):
        calls.append(start)
        if start == 0:
            cancel.set()  # the user hits Cancel during the first segment
            raise DownloadCancelled
        # Occupy the worker. Without this a segment that fails instantly frees
        # its thread, which grabs the next queued item before the main thread
        # has even seen the first failure - so the count would measure
        # scheduling luck rather than whether the queue was dropped.
        time.sleep(0.5)
        raise DownloadCancelled

    monkeypatch.setattr(dr, "_fetch_segment", _fake_fetch)
    respx.get(f"{SERVER}/api/files/fid/download").mock(
        return_value=httpx.Response(
            206,
            content=DATA[1:2],
            headers={"Content-Range": f"bytes 1-1/{len(DATA)}", "ETag": ETAG},
        )
    )
    dest = tmp_path / "out.bin"

    with pytest.raises(DownloadCancelled):
        dr.download_file_resumable(
            _api(), "fid", dest=dest, connections=2, cancel=cancel
        )

    # Not an exact count: how many are already in flight when the shutdown
    # lands is a scheduling detail. All five is the thing that cannot happen -
    # that is the queue being drained instead of dropped.
    assert 0 in calls
    assert len(calls) < 5, f"every queued segment still ran after cancel: {calls}"
    assert not dest.exists()
    assert not ck.part_path(dest).exists()  # cancel discards the partial
    assert not ck.ckpt_path(dest).exists()


@respx.mock
def test_a_hard_failure_stops_the_other_segments_instead_of_waiting_for_them(
    tmp_path, monkeypatch
):
    """The costly one. On a hard failure neither cancel nor pause is set, so
    the old code signalled nothing and shutdown(wait=True) then waited for every
    other segment to download its FULL span before raising the error it already
    had - on a 30 GB file, the rest of the transfer, thrown away.

    The segments are faked rather than served through respx: respx serialises
    handler dispatch, so one blocked handler stalls the others and the test
    would measure respx rather than the orchestrator. The fake keeps the part
    that matters - a stop check per chunk, exactly like _fetch_segment."""
    monkeypatch.setattr(dr, "SEGMENT_THRESHOLD", 10)
    monkeypatch.setattr(dr, "SEGMENT_SIZE", 10)

    slow_entered = threading.Event()
    stopped_early = threading.Event()
    ran_to_completion = threading.Event()
    slow = 20.0  # stands in for "the rest of a large transfer"

    def _fake_fetch(api, url, headers, part, start, end, bump, cancel, pause):
        if start == 0:
            raise OSError("segment 0: permanent failure after retries")
        slow_entered.set()
        deadline = time.monotonic() + slow
        while time.monotonic() < deadline:
            if (cancel is not None and cancel.is_set()) or (
                pause is not None and pause.is_set()
            ):
                stopped_early.set()
                raise DownloadPaused
            time.sleep(0.01)
        ran_to_completion.set()

    monkeypatch.setattr(dr, "_fetch_segment", _fake_fetch)
    respx.get(f"{SERVER}/api/files/fid/download").mock(
        return_value=httpx.Response(
            206,
            content=DATA[1:2],
            headers={"Content-Range": f"bytes 1-1/{len(DATA)}", "ETag": ETAG},
        )
    )
    dest = tmp_path / "out.bin"

    started = time.monotonic()
    with pytest.raises(OSError):
        dr.download_file_resumable(_api(), "fid", dest=dest, connections=4)
    elapsed = time.monotonic() - started

    assert slow_entered.is_set(), "no segment ever blocked; the test proved nothing"
    assert elapsed < slow / 2, (
        f"waited {elapsed:.1f}s for the other segments before raising"
    )
    # The partial and its checkpoint survive, so the user can resume.
    assert ck.ckpt_path(dest).exists()

    # And the stop signal actually REACHED the running segments - the half the
    # old code could not do, because the `.set()` ran after the pool had
    # already drained. Without it they would keep downloading in the
    # background, holding the .part file open and the bandwidth with it.
    for _ in range(200):
        if stopped_early.is_set():
            break
        time.sleep(0.01)
    assert stopped_early.is_set(), "in-flight segments were never told to stop"
    assert not ran_to_completion.is_set()


def test_the_stop_signal_is_separate_from_the_callers_events():
    """The nudge must not be the caller's cancel/pause. The UI owns those and
    reads them back to decide what it is showing, so setting one to make a
    worker exit would tell the user they had paused a download that failed."""
    caller_pause = threading.Event()
    internal = threading.Event()
    either = dr._EitherEvent(caller_pause, internal)

    assert not either.is_set()
    internal.set()
    assert either.is_set()
    assert not caller_pause.is_set(), "the internal stop leaked into the caller's event"

    # And it still honours the caller's own signal.
    assert dr._EitherEvent(caller_pause, threading.Event()).is_set() is False
    caller_pause.set()
    assert dr._EitherEvent(caller_pause, threading.Event()).is_set() is True
    # Absent caller event (the UI passed none) must not blow up.
    assert dr._EitherEvent(None, threading.Event()).is_set() is False
