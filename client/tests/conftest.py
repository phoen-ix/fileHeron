"""Test fixtures.

Tests deliberately avoid importing the ``ui`` package at module scope: that
pulls in CustomTkinter, hence tkinter, which the LINUX CI image does not have -
the UI tests are AST/structural for exactly this reason. Pytest discovers
modules under ``tests/`` only, so as long as we don't ``import
fileheron_client.ui`` here we are fine.

The Windows runner DOES have Tcl/Tk, and this premise was carried over to it
unexamined when the matrix gained that leg. ``test_windows_divergence.py``
imports every UI module there - the closest CI gets to launching the app, and a
gate no structural test can be.

(This said "PySide6" until the 2026-07-30 audit. The client has never shipped
Qt; the toolkit decision was CustomTkinter from the first commit, and a wrong
framework name in the file that explains WHY the tests are shaped this way is
how someone concludes the constraint no longer applies.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

# Allow ``import fileheron_client...`` without `pip install -e .`
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def tmp_keyring(monkeypatch) -> Iterator[dict]:
    """In-memory keyring backend, isolated per test."""
    store: dict[str, str] = {}

    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class _MemKeyring(KeyringBackend):
        priority = 1  # noqa: F841 - required by KeyringBackend metaclass

        def get_password(self, service: str, username: str) -> str | None:
            return store.get(f"{service}\x00{username}")

        def set_password(self, service: str, username: str, value: str) -> None:
            store[f"{service}\x00{username}"] = value

        def delete_password(self, service: str, username: str) -> None:
            key = f"{service}\x00{username}"
            if key not in store:
                raise PasswordDeleteError(f"no entry for {username}")
            del store[key]

    keyring.set_keyring(_MemKeyring())
    yield store


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect platformdirs to tmp_path so tests don't write
    ~/.config/fileheron."""
    monkeypatch.setattr(
        "platformdirs.user_config_dir",
        lambda *_args, **_kwargs: str(tmp_path),
    )
    return tmp_path
