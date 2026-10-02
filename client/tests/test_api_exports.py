"""Every `api_pkg.<name>` the app calls must exist in `fileheron_client.api`.

`from .. import api as api_pkg` binds the package; the attribute is looked up
only when the call runs - inside a background worker whose failure handler may
swallow it. That is how the share page's public-link section never rendered
from client v0.5.3 to v1.5.0: `share_detail_view` called
`api_pkg.get_public_link`, which `api/__init__.py` never exported, and the
`AttributeError` died silently in the worker. No import fails and no
structural test noticed, so this checks every such name, everywhere.

The second half pins where that section lands once it does render.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"


def _api_pkg_names() -> dict[str, list[str]]:
    """name -> the places it is used, over every module in the package."""
    used: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "api_pkg"
            ):
                used.setdefault(node.attr, []).append(
                    f"{path.relative_to(SRC)}:{node.lineno}"
                )
    return used


def test_every_api_pkg_name_the_app_uses_is_exported():
    import fileheron_client.api as api

    used = _api_pkg_names()
    assert len(used) >= 20 and "get_public_link" in used, (
        f"the scan went vacuous: {sorted(used)}"
    )
    missing = {
        name: where for name, where in used.items()
        if name not in api.__all__ or not hasattr(api, name)
    }
    assert not missing, (
        "called through api_pkg but not exported by fileheron_client.api "
        f"(fails only at call time, often silently in a worker): {missing}"
    )


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{cls.name}.{name} not found")


def test_the_public_link_section_is_placed_above_the_files():
    """The section is packed when its fetch returns, and pack() appends - so
    without `before=` it lands under the action buttons."""
    path = SRC / "ui" / "share_detail_view.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ShareDetailView")
    build = ast.unparse(_method(cls, "_build"))
    assert "self._files_heading = ctk.CTkLabel(outer, text=t('share_detail.files_heading')" in build
    render = _method(cls, "_render_public_link")
    packs = [n for n in ast.walk(render) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "pack"]
    targets = {ast.unparse(n.func.value): n for n in packs}
    assert {"self._pl_section_label", "self._pl_section"} <= set(targets)
    for name in ("self._pl_section_label", "self._pl_section"):
        before = [k for k in targets[name].keywords if k.arg == "before"]
        assert before and ast.unparse(before[0].value) == "self._files_heading", name


def test_the_action_row_keeps_its_place_on_a_short_window():
    """pack() hands out space in packing order: a row packed last is the
    first thing squeezed off the window. With the public-link section shown,
    End share / Save all fell below the edge of the default 1000x640, so the
    row is packed first, at the bottom, before the file list."""
    path = SRC / "ui" / "share_detail_view.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ShareDetailView")
    build = ast.unparse(_method(cls, "_build"))
    row = build.find("btns.pack(side='bottom', fill='x')")
    heading = build.find("self._files_heading.pack(")
    files = build.find("self.file_scroll.pack(")
    assert -1 < row < heading < files, (row, heading, files)
