"""Opening a SERVER-supplied link in the browser (audit 2026-11-19).

The public-link buttons opened whatever URL the API returned with
``webbrowser.open``. On Windows that ends in ``os.startfile``, which does not
care whether the string is a web address: a local path or a UNC path such as
``\\\\host\\share\\x.exe`` is EXECUTED. The server names this value, and the
client already treats server-supplied names as hostile (``safe_path``), so only
an http(s) URL with a host is handed to the browser.

Kept tkinter-free so it is importable and unit-testable on the Linux CI leg.
"""
from __future__ import annotations

import webbrowser
from urllib.parse import urlsplit

_WEB_SCHEMES = ("https", "http")


def is_web_url(url: str | None) -> bool:
    """True for an absolute http(s) URL with a host and nothing a shell or the
    Win32 path layer would read differently (backslashes, control characters)."""
    if not url or "\\" in url or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in _WEB_SCHEMES and bool(parts.hostname)


def open_web_url(url: str | None) -> bool:
    """Open ``url`` in the default browser if it is a web URL. Returns whether
    it was handed over; never raises."""
    if url is None or not is_web_url(url):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
