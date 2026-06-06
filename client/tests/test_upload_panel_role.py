"""Structural guard: the desktop upload panel is role-aware (client-v0.9.16).

AST/substring only (CI lacks system tkinter). A client submits to the company -
no recipient picker, kind="inbound", recipients empty; staff keep the outbound
picker flow.
"""
from __future__ import annotations

import ast
from pathlib import Path

PANEL = (
    Path(__file__).resolve().parent.parent
    / "src" / "fileheron_client" / "ui" / "upload_panel.py"
)


def _src() -> str:
    return PANEL.read_text(encoding="utf-8")


def test_panel_init_takes_me_and_sets_is_client() -> None:
    src = _src()
    tree = ast.parse(src)
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "UploadPanel"
    )
    init = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    arg_names = {a.arg for a in init.args.args}
    assert "me" in arg_names, "UploadPanel.__init__ must accept `me` (role source)"
    assert "self._is_client" in src, "panel must derive _is_client from role"


def test_client_sends_inbound_without_recipients() -> None:
    src = _src()
    # kind chosen by role; recipients empty for clients.
    assert '"inbound" if self._is_client else "outbound"' in src
    assert "[] if self._is_client else self.recipients.user_ids()" in src
    # The recipient-required guard is skipped for clients.
    assert "not self._is_client" in src
