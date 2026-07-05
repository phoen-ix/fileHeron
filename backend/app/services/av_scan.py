"""ClamAV scanning over the network.

Two scan paths:
- ``scan_path(abs_path)`` - sends ``zSCAN /abs/path\\0`` to clamd. clamd
  reads the file directly. Requires the file to be visible at the same
  absolute path inside the clamav container (we bind-mount ./data/files
  into both backend and clamav at /data/files, so this just works).
- ``scan_stream(file_handle)`` - INSTREAM fallback if path-based scan
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


class AVUnavailableError(Exception):
    """Raised when clamd is unreachable. Caller decides whether to retry
    (worker does) or to fall through to a permissive default."""


def _open_clamd_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(SOCKET_TIMEOUT_SEC)
    try:
        s.connect((settings.CLAMAV_HOST, settings.CLAMAV_PORT))
    except OSError as e:
        s.close()
        raise AVUnavailableError(
            f"cannot connect to {settings.CLAMAV_HOST}:{settings.CLAMAV_PORT}: {e}"
        ) from e
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

    Raises AVUnavailableError if clamd is unreachable. All other failures (e.g.
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


_INSTREAM_CHUNK = 64 * 1024


def scan_stream(fh) -> ScanResult:
    """Scan a readable binary stream via clamd INSTREAM - used when the bytes
    aren't on a clamd-visible local path (object-store backends). `fh` is any
    object with ``.read(n)``.

    Caveat: INSTREAM is bounded by clamd's ``StreamMaxLength`` (default 25 MB).
    Operators using the s3 backend with larger files must raise it; a file that
    exceeds the limit comes back as ``state='error'`` (fail-safe - not served)."""
    import struct

    if settings.AV_SKIP:
        return ScanResult(state="clean", signature=None, raw="AV_SKIP set")

    s = _open_clamd_socket()
    try:
        try:
            s.sendall(b"zINSTREAM\0")
            while True:
                chunk = fh.read(_INSTREAM_CHUNK)
                if not chunk:
                    break
                s.sendall(struct.pack("!I", len(chunk)) + chunk)
            s.sendall(struct.pack("!I", 0))  # zero-length chunk terminates the stream
            reply = _read_reply(s)
            return _parse_reply(reply, prefix="stream")
        except OSError as e:
            # clamd closes the connection mid-stream once the bytes exceed
            # StreamMaxLength (or on any transport hiccup), so the next sendall
            # raises BrokenPipe/ConnectionReset/Timeout. Fail SAFE with
            # state='error' (the documented, not-served outcome) instead of
            # raising - otherwise a single oversize/hostile attachment aborts the
            # whole IMAP poll (and every other scan caller). Read clamd's parting
            # message if it sent one ("INSTREAM size limit exceeded").
            try:
                trailer = _read_reply(s)
            except OSError:
                trailer = ""
            logger.warning("INSTREAM scan transport error: %s (reply=%r)", e, trailer)
            return ScanResult(
                state="error", signature=None,
                raw=trailer or f"instream transport error: {e}",
            )
    finally:
        s.close()


def ping() -> bool:
    """Healthcheck - returns True if clamd answers PONG."""
    try:
        s = _open_clamd_socket()
    except AVUnavailableError:
        return False
    try:
        s.sendall(b"zPING\0")
        return _read_reply(s).strip() == "PONG"
    finally:
        s.close()


def get_version() -> dict:
    """Ask clamd for its VERSION string + parse out daemon + sig info.

    Returns a dict shaped for the admin-UI ``AvStatusResponse`` schema:

        {
            "available": bool,
            "av_skip": bool,
            "version": str | None,        # e.g. "ClamAV 1.5.2"
            "sigs_version": str | None,   # e.g. "27543" (sig revision)
            "sigs_date": str | None,      # ctime-style, e.g. "Fri Apr 26 10:23:45 2026"
            "raw": str | None,            # full reply for debugging
            "error": str | None,          # populated when available=False
        }

    Short-circuits to ``available=False, av_skip=True`` when ``AV_SKIP``
    is set (dev / CI mode - no clamd running). Never raises; the admin
    surface always wants to render *something*."""
    if settings.AV_SKIP:
        return {
            "available": False,
            "av_skip": True,
            "version": None,
            "sigs_version": None,
            "sigs_date": None,
            "raw": None,
            "error": None,
        }
    try:
        s = _open_clamd_socket()
    except AVUnavailableError as e:
        return {
            "available": False,
            "av_skip": False,
            "version": None,
            "sigs_version": None,
            "sigs_date": None,
            "raw": None,
            "error": str(e),
        }
    try:
        s.sendall(b"zVERSION\0")
        raw = _read_reply(s).strip()
    finally:
        s.close()
    # Format: "ClamAV <ver>/<sig_revision>/<ctime-date>", split on '/'.
    parts = [p.strip() for p in raw.split("/")]
    version = parts[0] if parts and parts[0] else None
    sigs_version = parts[1] if len(parts) > 1 and parts[1] else None
    sigs_date = parts[2] if len(parts) > 2 and parts[2] else None
    return {
        "available": bool(version),
        "av_skip": False,
        "version": version,
        "sigs_version": sigs_version,
        "sigs_date": sigs_date,
        "raw": raw or None,
        "error": None if version else "empty VERSION reply",
    }


def reload_signatures() -> dict:
    """Ask clamd to re-read its signature DB from disk. Useful after
    freshclam has fetched updates and we want them to land in the
    running engine without a container restart.

    Returns ``{"ok": bool, "av_skip": bool, "raw": str}``. Short-circuits
    on ``AV_SKIP``. Raises AVUnavailableError if clamd is unreachable - the
    router converts that into a 503 ``AV_UNAVAILABLE`` response."""
    if settings.AV_SKIP:
        return {"ok": False, "av_skip": True, "raw": "AV_SKIP set"}
    s = _open_clamd_socket()
    try:
        s.sendall(b"zRELOAD\0")
        raw = _read_reply(s).strip()
    finally:
        s.close()
    return {
        "ok": raw.upper().startswith("RELOAD"),
        "av_skip": False,
        "raw": raw,
    }
