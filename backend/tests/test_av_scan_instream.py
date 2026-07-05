"""Regression: scan_stream must fail SAFE (state='error') when clamd drops the
connection mid-INSTREAM (e.g. bytes exceed StreamMaxLength) - it previously let
the BrokenPipeError propagate, aborting the whole IMAP poll and every scan caller.
"""
from __future__ import annotations

import io

from app.services import av_scan


def test_scan_stream_transport_break_returns_error_not_raise(monkeypatch):
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", False)

    class FakeSock:
        def __init__(self):
            self.sends = 0

        def sendall(self, data):
            self.sends += 1
            # zINSTREAM handshake ok; clamd then closes at the size limit, so the
            # first data chunk's sendall raises (as the real BrokenPipeError does).
            if self.sends >= 2:
                raise BrokenPipeError("clamd closed the stream (size limit)")

        def recv(self, _n):
            raise ConnectionResetError("closed")

        def close(self):
            pass

    monkeypatch.setattr(av_scan, "_open_clamd_socket", lambda: FakeSock())

    result = av_scan.scan_stream(io.BytesIO(b"x" * 200_000))
    assert result.state == "error"  # fail-safe, not a raised BrokenPipeError
