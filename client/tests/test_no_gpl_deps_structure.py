"""The published .exe must not link GPL code, and the date picker must be ours.

tkcalendar is GPL-3.0 and was a declared runtime dependency compiled into the
MIT-licensed .exe published for download - a licence conflict in a shipped
artifact (audit 2026-07-30). It is replaced by
``fileheron_client.ui.date_entry.DateEntry`` on stdlib tkinter + CustomTkinter.

AST/substring checks only: CI has no system tkinter, so the ui package cannot be
imported here. Same approach as the other *_structure tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "src" / "fileheron_client" / "ui"

# Anything whose licence would contaminate the MIT .exe.
BANNED = ("tkcalendar",)


def _py_files() -> list[Path]:
    return list((ROOT / "src").rglob("*.py"))


def test_no_banned_import_anywhere_in_the_client():
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for banned in BANNED:
                if banned in names:
                    offenders.append(f"{path.relative_to(ROOT)}: {banned}")
    assert not offenders, f"GPL dependency imported into the MIT client: {offenders}"


def test_not_declared_as_a_dependency():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps_block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    for banned in BANNED:
        # Mentioned in a comment explaining the removal is fine; declared is not.
        declared = [
            ln for ln in deps_block.splitlines()
            if banned in ln and not ln.strip().startswith("#")
        ]
        assert not declared, f"{banned} is still a declared dependency: {declared}"


def test_not_bundled_by_pyinstaller():
    spec = (ROOT / "pyinstaller.spec").read_text(encoding="utf-8")
    for banned in BANNED:
        assert f'collect_data_files("{banned}")' not in spec
        assert f'"{banned}",' not in spec, f"{banned} still in hiddenimports"


def test_date_entry_exposes_the_api_the_callers_use():
    tree = ast.parse((UI / "date_entry.py").read_text(encoding="utf-8"))
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DateEntry"),
        None,
    )
    assert cls is not None, "DateEntry class missing"
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    # Exactly what expiry_dialog.py and upload_panel.py call on it.
    for required in ("get_date", "set_date", "configure"):
        assert required in methods, f"DateEntry.{required} missing"


def test_both_call_sites_use_the_in_tree_widget():
    for name in ("expiry_dialog.py", "upload_panel.py"):
        src = (UI / name).read_text(encoding="utf-8")
        assert "from .date_entry import DateEntry" in src, name
        assert "tkcalendar" not in src, name


def test_popup_avoids_the_ctktoplevel_titlebar_trap():
    """CTkToplevel's titlebar-colour routine withdraws and re-shows the window,
    and that deiconify can get lost mid-construction, leaving it invisible. The
    picker popup must use a plain tk.Toplevel."""
    src = (UI / "date_entry.py").read_text(encoding="utf-8")
    assert "tk.Toplevel(" in src
    assert "ctk.CTkToplevel(" not in src
