"""Structural guards for the v0.12.0 desktop-client branding logo.

AST/substring checks so no ``ui`` import is needed (CI may lack the system
tkinter that customtkinter requires). Pins the contract:

- ``api/branding.py`` exposes ``branding_logo_png``.
- ``MainWindow`` fetches the logo in the background (run_in_background) and
  applies it via a ``_logo_label`` / PhotoImage, gated behind post_show.
- It never shadows ``tkinter.Misc`` attrs (no ``self._root = ...``).
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"
UI = PKG / "ui"
API = PKG / "api"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _has_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _method_names(cls: ast.ClassDef) -> set[str]:
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}


def test_api_defines_branding_logo_png() -> None:
    tree = _tree(API / "branding.py")
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "branding_logo_png" in funcs


def test_main_window_loads_and_applies_logo() -> None:
    cls = _has_class(_tree(UI / "main_window.py"), "MainWindow")
    names = _method_names(cls)
    assert "_load_branding_logo" in names
    assert "_apply_branding_logo" in names


def test_main_window_uses_background_fetch_and_photoimage() -> None:
    src = _source(UI / "main_window.py")
    assert "branding_logo_png" in src
    assert "run_in_background" in src
    assert "PhotoImage" in src
    assert "_logo_label" in src
    assert "_logo_image" in src


def test_post_show_kicks_the_logo_load() -> None:
    src = _source(UI / "main_window.py")
    # The fetch must be triggered from post_show (after the overlay is gone).
    assert "self._load_branding_logo()" in src


def test_main_window_does_not_shadow_root_attr() -> None:
    src = _source(UI / "main_window.py")
    assert "self._root =" not in src, "must not shadow tkinter.Misc._root"
