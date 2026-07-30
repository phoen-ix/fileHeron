"""Liveness probes must not inherit the clamd SCAN timeout.

Regression cover for a defect introduced by the v2.2.0 scan-timeout fix itself.
Raising SOCKET_TIMEOUT_SEC to 1800 s (so multi-GB scans stop timing out) also
raised it for `ping()`, because both went through the same `_open_clamd_socket`.
`routers/health.py` calls `ping()` on every `/api/health` request, and that
endpoint is anonymous and unthrottled - so against an UNRESPONSIVE clamd
(connection accepted, no reply) each request began pinning a worker thread for
up to 30 minutes instead of 60 seconds. A cheap unauthenticated
resource-exhaustion primitive, created by a fix.

The property under test: a probe's ceiling stays small and independent of the
scan ceiling, whatever anyone later does to the latter.
"""
from __future__ import annotations

import pytest

from app.services import av_scan


class _FakeSocket:
    """Records settimeout() so the test can assert on the ceiling actually used."""

    last_timeout: float | None = None

    def __init__(self, *_a, **_kw):
        self.sent: list[bytes] = []
        self._reply = b"PONG\0"

    def settimeout(self, t):
        type(self).last_timeout = t

    def connect(self, _addr):
        return None

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, _n):
        r, self._reply = self._reply, b""
        return r

    def close(self):
        return None


@pytest.fixture
def fake_socket(monkeypatch):
    _FakeSocket.last_timeout = None
    monkeypatch.setattr(av_scan.socket, "socket", _FakeSocket)
    return _FakeSocket


def test_ping_uses_the_short_probe_timeout(fake_socket):
    assert av_scan.ping() is True
    assert fake_socket.last_timeout == av_scan.PING_TIMEOUT_SEC


def test_get_version_uses_the_short_probe_timeout(fake_socket, monkeypatch):
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", False)
    av_scan.get_version()
    assert fake_socket.last_timeout == av_scan.PING_TIMEOUT_SEC


def test_scans_keep_the_long_timeout(fake_socket, monkeypatch, tmp_path):
    """Control: the scan path must NOT be shortened - that would re-break the
    multi-GB scans the long timeout exists for."""
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", False)
    f = tmp_path / "x.bin"
    f.write_bytes(b"data")
    av_scan.scan_path(str(f))
    assert fake_socket.last_timeout == av_scan.SOCKET_TIMEOUT_SEC


def test_probe_ceiling_stays_far_below_the_scan_ceiling():
    """The two must not be re-coupled. A probe blocking for minutes on an
    anonymous endpoint is the whole defect; keep an explicit, order-of-magnitude
    gap so a future edit to one cannot silently drag the other along."""
    assert av_scan.PING_TIMEOUT_SEC <= 15
    assert av_scan.PING_TIMEOUT_SEC < av_scan.SOCKET_TIMEOUT_SEC / 10
