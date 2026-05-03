"""ClamAV scanning over the network.

Two scan paths:
- ``scan_path(abs_path)`` — sends ``zSCAN /abs/path\\0`` to clamd. clamd
  reads the file directly. Requires the file to be visible at the same
  absolute path inside the clamav container (we bind-mount ./data/files
  into both backend and clamav at /data/files, so this just works).
- ``scan_stream(file_handle)`` — INSTREAM fallback if path-based scan
  refuses (e.g. permissions). Used by tests + a fallback in production.

We deliberately do not pull in pyclamd / clamd. The protocol is small,
the error surface is narrow, and a 70-line dependency-free client is
easier to reason about than a wrapper that has its own quirks.
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

logger = logging.getLogger("fileheron.av_scan")

CHUNK_SIZE = 1024 * 64  # clamd recommends 64 KiB chunks
SOCKET_TIMEOUT_SEC = 60.0  # large files take a while to scan


@dataclass(frozen=True)
class ScanResult:
    """Result of a single scan request."""
    state: str  # "clean" | "infected" | "error"
    signature: str | None  # virus signature if infected
    raw: str  # full clamd reply line for the audit log


class AVUnavailable(Exception):
    """Raised when clamd is unreachable. Caller decides whether to retry
    (worker does) or to fall through to a permissive default."""


def _open_clamd_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(SOCKET_TIMEOUT_SEC)
    try:
        s.connect((settings.CLAMAV_HOST, settings.CLAMAV_PORT))
    except OSError as e:
        s.close()
        raise AVUnavailable(f"cannot connect to {settings.CLAMAV_HOST}:{settings.CLAMAV_PORT}: {e}")
    return s


def _read_reply(s: socket.socket) -> str:
    """Read until clamd closes the connection or sends a NUL terminator."""
    chunks: list[bytes] = []
    while True:
        try:
            data = s.recv(4096)
        except TimeoutError:
            break
        if not data:
            break
        chunks.append(data)
        if b"\0" in data:
            break
    return b"".join(chunks).rstrip(b"\0").decode("utf-8", errors="replace").strip()


def _parse_reply(reply: str, prefix: str) -> ScanResult:
    """clamd `SCAN` and `INSTREAM` produce identical reply shapes:
        "<path>: OK"
        "<path>: <signature> FOUND"
        "<error>: ERROR"
    """
    raw = reply
    if reply.endswith(": OK"):
        return ScanResult(state="clean", signature=None, raw=raw)
    if reply.endswith(" FOUND"):
        # ".../foo: Eicar-Test-Signature FOUND"
        # Drop the path prefix + the trailing " FOUND".
        body = reply[: -len(" FOUND")]
        sig = body.split(": ", 1)[-1] if ": " in body else body
        return ScanResult(state="infected", signature=sig, raw=raw)
    return ScanResult(state="error", signature=None, raw=raw or f"{prefix} returned empty reply")


def scan_path(abs_path: str) -> ScanResult:
    """Ask clamd to scan a file at the given absolute path. The path must
    be visible inside the clamav container at the same absolute path.

    Raises AVUnavailable if clamd is unreachable. All other failures (e.g.
    file unreadable to clamd) come back as ScanResult(state='error')."""
    if not Path(abs_path).is_file():
        return ScanResult(state="error", signature=None, raw=f"file not found: {abs_path}")
    if settings.AV_SKIP:
        return ScanResult(state="clean", signature=None, raw="AV_SKIP set")

    s = _open_clamd_socket()
    try:
        # zSCAN expects the path NUL-terminated; null-byte safe across
        # filenames with spaces.
        s.sendall(b"zSCAN " + abs_path.encode("utf-8") + b"\0")
        reply = _read_reply(s)
        return _parse_reply(reply, prefix=abs_path)
    finally:
        s.close()


def ping() -> bool:
    """Healthcheck — returns True if clamd answers PONG."""
    try:
        s = _open_clamd_socket()
    except AVUnavailable:
        return False
    try:
        s.sendall(b"zPING\0")
        return _read_reply(s).strip() == "PONG"
    finally:
        s.close()
