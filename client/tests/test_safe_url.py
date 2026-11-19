"""Only a web URL from the server reaches the browser (audit 2026-11-19).

On Windows ``webbrowser.open`` ends in ``os.startfile``, which executes a local
or UNC path as readily as it opens a web page, and the public-link URL the
buttons open is named by the server.
"""
from __future__ import annotations

import pytest

from fileheron_client import safe_url


@pytest.mark.parametrize(
    "url",
    [
        "https://files.example.com/d/abc123",
        "http://127.0.0.1:8080/d/abc123",
        "HTTPS://Files.Example.com/d/x",
    ],
)
def test_web_urls_are_accepted(url):
    assert safe_url.is_web_url(url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        r"C:\Windows\System32\calc.exe",
        r"\\attacker\share\payload.exe",
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(1)",
        "ms-settings:privacy",
        "https:\\\\attacker\\share\\x.exe",
        "https://",
        "https://ok.example.com/\nC:\\x.exe",
        "//files.example.com/d/abc",
        "calc.exe",
    ],
)
def test_anything_else_is_refused(url):
    assert not safe_url.is_web_url(url)


def test_open_hands_only_web_urls_to_the_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(safe_url.webbrowser, "open", lambda u: opened.append(u) or True)

    assert safe_url.open_web_url(r"\\attacker\share\payload.exe") is False
    assert safe_url.open_web_url("https://files.example.com/d/abc") is True
    assert opened == ["https://files.example.com/d/abc"]


def test_a_browser_failure_never_raises(monkeypatch):
    def boom(_u):
        raise OSError("no browser")

    monkeypatch.setattr(safe_url.webbrowser, "open", boom)
    assert safe_url.open_web_url("https://files.example.com/d/abc") is False


def test_both_link_buttons_go_through_the_guard():
    from pathlib import Path

    ui = Path(safe_url.__file__).parent / "ui"
    for name in ("share_detail_view.py", "upload_progress_view.py"):
        src = (ui / name).read_text(encoding="utf-8")
        assert "open_web_url(" in src, name
        assert "webbrowser" not in src.replace("webbrowser.open would", ""), name
