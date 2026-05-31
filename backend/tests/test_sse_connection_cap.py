"""Regression: per-user SSE connection cap (finding M4)."""
from __future__ import annotations

from app.services import sse as sse_svc


def test_acquire_release_caps_per_user():
    uid = 4242
    # Reset any residue.
    sse_svc._active_streams.pop(uid, None)

    # Acquire up to the cap.
    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        assert sse_svc.try_acquire_user_stream(uid) is True
    # One past the cap is refused.
    assert sse_svc.try_acquire_user_stream(uid) is False

    # Releasing one frees a slot.
    sse_svc.release_user_stream(uid)
    assert sse_svc.try_acquire_user_stream(uid) is True

    # Drain back to zero — the key is removed (no unbounded dict growth).
    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        sse_svc.release_user_stream(uid)
    assert uid not in sse_svc._active_streams
    # Over-release is harmless.
    sse_svc.release_user_stream(uid)
    assert uid not in sse_svc._active_streams


def test_cap_is_per_user():
    a, b = 111, 222
    sse_svc._active_streams.pop(a, None)
    sse_svc._active_streams.pop(b, None)
    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        assert sse_svc.try_acquire_user_stream(a) is True
    # User A is full but user B is unaffected.
    assert sse_svc.try_acquire_user_stream(a) is False
    assert sse_svc.try_acquire_user_stream(b) is True
    # cleanup
    for _ in range(sse_svc.MAX_STREAMS_PER_USER + 1):
        sse_svc.release_user_stream(a)
        sse_svc.release_user_stream(b)
