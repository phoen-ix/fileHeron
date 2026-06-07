"""H4: the backend reduces every client-supplied filename to a safe leaf, so a
path-traversal payload can never be persisted (defense-in-depth protecting the
desktop 'Save all', ZIP entry names, and Content-Disposition)."""
from __future__ import annotations

import pytest

from app.services.file import safe_original_filename


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\System32\\evil.bat", "evil.bat"),
        ("/abs/path/report.pdf", "report.pdf"),
        ("normal.txt", "normal.txt"),
        ("..", "file"),
        (".", "file"),
        ("", "file"),
        ("a/b/c/", "file"),
        ("with\x00nul.txt", "withnul.txt"),
        (".gitignore", ".gitignore"),
        ("archive.tar.gz", "archive.tar.gz"),
    ],
)
def test_safe_original_filename(raw, expected):
    assert safe_original_filename(raw) == expected
