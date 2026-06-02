"""Structural guards for the right-click context menu (client-v0.9.13).

AST/substring only — CI lacks system tkinter (CustomTkinter needs it), so we
don't import ``ui``. Mirrors ``test_login_overlay_structure.py``.

The i18n keys (``context_menu.cut`` etc.) are covered by ``test_i18n.py``'s
lockstep gate, which collects every ``t('...')`` call and asserts en + de both
define it.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"
UI = PKG / "ui"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _func_names(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_context_menu_module_defines_installer_and_handler() -> None:
    tree = ast.parse(_source(UI / "context_menu.py"))
    names = _func_names(tree)
    assert {"install_context_menus", "_show_menu"} <= names, (
        f"context_menu.py must define install_context_menus + _show_menu; got {names}"
    )


def test_context_menu_binds_right_click_and_uses_virtual_events() -> None:
    src = _source(UI / "context_menu.py")
    assert "bind_class" in src, "must install via bind_class (interpreter-global)"
    assert "<Button-3>" in src, "must bind the right-click event"
    for ev in ("<<Cut>>", "<<Copy>>", "<<Paste>>"):
        assert ev in src, f"must fire the {ev} virtual event for correct clipboard behaviour"


def test_build_root_installs_context_menus() -> None:
    src = _source(UI / "app.py")
    assert "install_context_menus" in src, (
        "app.py build_root must call install_context_menus on the root"
    )
